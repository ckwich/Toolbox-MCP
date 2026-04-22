from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

import anyio
import anyio.lowlevel
import mcp.types as types
from anyio.abc import Process
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from anyio.streams.text import TextReceiveStream
from mcp.client.stdio import StdioServerParameters, get_default_environment
from mcp.os.posix.utilities import terminate_posix_process_tree
from mcp.os.win32.utilities import (
    FallbackProcess,
    create_windows_process,
    get_windows_executable_command,
    terminate_windows_process_tree,
)
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)

PROCESS_TERMINATION_TIMEOUT = 2.0


@asynccontextmanager
async def stdio_client(server: StdioServerParameters, errlog: TextIO = sys.stderr):
    """Spawn an MCP server over stdio with explicit Windows transport teardown."""

    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    try:
        process = await _create_platform_compatible_process(
            command=_get_executable_command(server.command),
            args=server.args,
            env=({**get_default_environment(), **server.env} if server.env is not None else get_default_environment()),
            errlog=errlog,
            cwd=server.cwd,
        )
    except OSError:
        await _close_memory_streams(read_stream, write_stream, read_stream_writer, write_stream_reader)
        raise

    async def stdout_reader() -> None:
        assert process.stdout, "Opened process is missing stdout"

        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in TextReceiveStream(
                    process.stdout,
                    encoding=server.encoding,
                    errors=server.encoding_error_handler,
                ):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:  # pragma: no cover
                            logger.exception("Failed to parse JSONRPC message from server")
                            await read_stream_writer.send(exc)
                            continue

                        await read_stream_writer.send(SessionMessage(message))
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdin_writer() -> None:
        assert process.stdin, "Opened process is missing stdin"

        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await process.stdin.send(
                        (payload + "\n").encode(
                            encoding=server.encoding,
                            errors=server.encoding_error_handler,
                        )
                    )
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdout_reader)
        task_group.start_soon(stdin_writer)
        try:
            yield read_stream, write_stream
        finally:
            await _shutdown_process(process)
            await _close_memory_streams(read_stream, write_stream, read_stream_writer, write_stream_reader)


async def _shutdown_process(process: Process | FallbackProcess) -> None:
    await _close_async_stream(getattr(process, "stdin", None))

    try:
        await _wait_for_process(process)
    except TimeoutError:
        await _terminate_process_tree(process)
        try:
            await _wait_for_process(process)
        except TimeoutError:
            await _force_close_process(process)

    await _close_process_streams(process)
    _close_underlying_subprocess_transport(process)
    await _drain_transport_close_callbacks(process)


async def _wait_for_process(process: Process | FallbackProcess) -> None:
    with anyio.fail_after(PROCESS_TERMINATION_TIMEOUT):
        await process.wait()


async def _force_close_process(process: Process | FallbackProcess) -> None:
    _close_underlying_subprocess_transport(process)

    if isinstance(process, FallbackProcess):
        try:
            process.kill()
        except Exception:
            pass

    try:
        await _wait_for_process(process)
    except TimeoutError:
        pass


async def _close_process_streams(process: Process | FallbackProcess) -> None:
    for stream_name in ("stdout", "stderr"):
        await _close_async_stream(getattr(process, stream_name, None))

    for handle_name in ("stdin_raw", "stdout_raw", "stderr"):
        _close_sync_handle(getattr(process, handle_name, None))


async def _close_async_stream(stream: object | None) -> None:
    if stream is None:
        return

    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return

    try:
        await aclose()
    except Exception:
        pass


def _close_sync_handle(handle: object | None) -> None:
    if handle is None:
        return

    close = getattr(handle, "close", None)
    if close is None:
        return

    try:
        close()
    except Exception:
        pass


def _close_underlying_subprocess_transport(process: Process | FallbackProcess) -> None:
    raw_process = getattr(process, "_process", None)
    transport = getattr(raw_process, "_transport", None)
    if transport is None or getattr(transport, "_closed", False):
        return

    # AnyIO closes the stdio stream wrappers, but on Windows we also need to
    # close the asyncio subprocess transport itself so its destructor does not
    # trip over already-closed pipe handles during GC.
    try:
        transport.close()
    except ProcessLookupError:
        return
    except Exception:
        logger.exception("Failed to close asyncio subprocess transport cleanly")


async def _drain_transport_close_callbacks(process: Process | FallbackProcess, turns: int = 5) -> None:
    raw_process = getattr(process, "_process", None)
    transport = getattr(raw_process, "_transport", None)
    if transport is None:
        await anyio.lowlevel.checkpoint()
        return

    for _ in range(turns):
        if _transport_close_callbacks_flushed(transport):
            return
        await anyio.lowlevel.checkpoint()


def _transport_close_callbacks_flushed(transport: object) -> bool:
    if not getattr(transport, "_closed", False):
        return False

    for proto in getattr(transport, "_pipes", {}).values():
        pipe = getattr(proto, "pipe", None)
        if pipe is None:
            continue
        if getattr(pipe, "_sock", None) is not None:
            return False
        if getattr(pipe, "_closing", False) and not getattr(pipe, "_called_connection_lost", True):
            return False

    return True


async def _close_memory_streams(*streams: object) -> None:
    for stream in streams:
        aclose = getattr(stream, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception:
            pass


def _get_executable_command(command: str) -> str:
    if sys.platform == "win32":  # pragma: no cover
        return get_windows_executable_command(command)
    return command  # pragma: no cover


async def _create_platform_compatible_process(
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    errlog: TextIO = sys.stderr,
    cwd: Path | str | None = None,
) -> Process | FallbackProcess:
    if sys.platform == "win32":  # pragma: no cover
        return await create_windows_process(command, args, env, errlog, cwd)

    return await anyio.open_process(
        [command, *args],
        env=env,
        stderr=errlog,
        cwd=cwd,
        start_new_session=True,
    )  # pragma: no cover


async def _terminate_process_tree(process: Process | FallbackProcess, timeout_seconds: float = 2.0) -> None:
    if sys.platform == "win32":  # pragma: no cover
        await terminate_windows_process_tree(process, timeout_seconds)
        return

    assert isinstance(process, Process)
    await terminate_posix_process_tree(process, timeout_seconds)  # pragma: no cover

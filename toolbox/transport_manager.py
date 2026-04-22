from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters

from toolbox.models import ToolSchema, ToolsetRecord
from toolbox.stdio_transport import stdio_client
from toolbox.validation import validate_tool_inventory


@dataclass
class RuntimeBootstrap:
    tools: list[types.Tool]
    normalized_tools: list[ToolSchema]


@dataclass
class RuntimeProbe:
    tools: list[types.Tool]
    normalized_tools: list[ToolSchema]


@dataclass
class RuntimeCommand:
    kind: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    future: asyncio.Future[Any] | None = None


class ManagedToolsetRuntime:
    """Owns a live downstream MCP session from a dedicated task."""

    def __init__(
        self,
        namespace: str,
        tools: list[types.Tool],
        queue: asyncio.Queue[RuntimeCommand],
        task: asyncio.Task[None],
        *,
        call_timeout_seconds: float,
        health_timeout_seconds: float,
        shutdown_timeout_seconds: float,
    ) -> None:
        self.namespace = namespace
        self.tools = tools
        self._queue = queue
        self._task = task
        self._call_timeout_seconds = call_timeout_seconds
        self._health_timeout_seconds = health_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._closed = False

    @property
    def is_running(self) -> bool:
        return not self._task.done() and not self._closed

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> types.CallToolResult:
        if not self.is_running:
            raise RuntimeError(f"Managed runtime for {self.namespace} is not available")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[types.CallToolResult] = loop.create_future()
        await self._queue.put(
            RuntimeCommand(
                kind="call_tool",
                tool_name=tool_name,
                arguments=arguments or {},
                future=future,
            )
        )

        try:
            return await asyncio.wait_for(future, timeout=self._call_timeout_seconds)
        except asyncio.TimeoutError as exc:
            future.cancel()
            await self._abort()
            raise TimeoutError(
                f"Mounted tool call timed out for {self.namespace}.{tool_name} after {self._call_timeout_seconds:.2f}s"
            ) from exc

    async def probe(self) -> RuntimeProbe:
        if not self.is_running:
            raise RuntimeError(f"Managed runtime for {self.namespace} is not available")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[RuntimeProbe] = loop.create_future()
        await self._queue.put(
            RuntimeCommand(
                kind="probe",
                future=future,
            )
        )

        try:
            return await asyncio.wait_for(future, timeout=self._health_timeout_seconds)
        except asyncio.TimeoutError as exc:
            future.cancel()
            await self._abort()
            raise TimeoutError(
                f"Health check timed out for {self.namespace} after {self._health_timeout_seconds:.2f}s"
            ) from exc

    async def close(self) -> None:
        if self._closed:
            await _drain_task(self._task)
            await _settle_runtime_callbacks()
            return

        self._closed = True
        if self._task.done():
            await _drain_task(self._task)
            await _settle_runtime_callbacks()
            return

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        await self._queue.put(RuntimeCommand(kind="close", future=future))
        try:
            await asyncio.wait_for(future, timeout=self._shutdown_timeout_seconds)
        except asyncio.TimeoutError:
            future.cancel()
            await self._abort()
        finally:
            await _drain_task(self._task)
            await _settle_runtime_callbacks()

    async def _abort(self) -> None:
        self._closed = True
        if not self._task.done():
            self._task.cancel()
        await _drain_task(self._task)
        await _settle_runtime_callbacks()


class TransportManager:
    """Owns live transport runtimes and in-flight counters."""

    def __init__(
        self,
        *,
        bootstrap_timeout_seconds: float = 10.0,
        call_timeout_seconds: float = 30.0,
        health_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self.bootstrap_timeout_seconds = bootstrap_timeout_seconds
        self.call_timeout_seconds = call_timeout_seconds
        self.health_timeout_seconds = health_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._runtimes: dict[str, ManagedToolsetRuntime] = {}
        self._in_flight = defaultdict(int)

    def budget_settings(self) -> dict[str, float]:
        return {
            "bootstrap_timeout_seconds": self.bootstrap_timeout_seconds,
            "mounted_tool_call_timeout_seconds": self.call_timeout_seconds,
            "health_check_timeout_seconds": self.health_timeout_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
        }

    async def open_runtime(self, record: ToolsetRecord) -> tuple[ManagedToolsetRuntime, list[ToolSchema]]:
        if record.transport.kind != "stdio":
            raise ValueError(f"Unsupported transport kind: {record.transport.kind}")

        queue: asyncio.Queue[RuntimeCommand] = asyncio.Queue()
        bootstrap_future: asyncio.Future[RuntimeBootstrap] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._runtime_actor(record, queue, bootstrap_future), name=f"toolbox-runtime:{record.namespace}")
        try:
            bootstrap = await asyncio.wait_for(bootstrap_future, timeout=self.bootstrap_timeout_seconds)
        except asyncio.TimeoutError as exc:
            task.cancel()
            await _drain_task(task)
            raise TimeoutError(
                f"Timed out bootstrapping managed runtime for {record.namespace} after {self.bootstrap_timeout_seconds:.2f}s"
            ) from exc
        except Exception:
            await _drain_task(task)
            raise

        runtime = ManagedToolsetRuntime(
            namespace=record.namespace,
            tools=bootstrap.tools,
            queue=queue,
            task=task,
            call_timeout_seconds=self.call_timeout_seconds,
            health_timeout_seconds=self.health_timeout_seconds,
            shutdown_timeout_seconds=self.shutdown_timeout_seconds,
        )
        return runtime, bootstrap.normalized_tools

    async def swap_runtime(self, namespace: str, runtime: ManagedToolsetRuntime) -> None:
        existing = self._runtimes.get(namespace)
        self._runtimes[namespace] = runtime
        if existing is not None:
            await existing.close()

    async def disconnect(self, namespace: str) -> bool:
        existing = self._runtimes.pop(namespace, None)
        if existing is None:
            return False
        await existing.close()
        return True

    def has_connection(self, namespace: str) -> bool:
        runtime = self._runtimes.get(namespace)
        return runtime is not None and runtime.is_running

    def runtime_for(self, namespace: str) -> ManagedToolsetRuntime | None:
        runtime = self._runtimes.get(namespace)
        if runtime is None or not runtime.is_running:
            return None
        return runtime

    def mounted_namespaces(self) -> list[str]:
        return sorted(namespace for namespace, runtime in self._runtimes.items() if runtime.is_running)

    async def call_tool(self, namespace: str, tool_name: str, arguments: dict[str, Any] | None = None) -> types.CallToolResult:
        runtime = self.runtime_for(namespace)
        if runtime is None:
            raise RuntimeError(f"Toolset {namespace} is not connected")
        return await runtime.call_tool(tool_name, arguments)

    async def probe_runtime(self, namespace: str) -> RuntimeProbe:
        runtime = self.runtime_for(namespace)
        if runtime is None:
            raise RuntimeError(f"Toolset {namespace} is not connected")
        return await runtime.probe()

    def in_flight_calls(self, namespace: str) -> int:
        return self._in_flight[namespace]

    def note_call_started(self, namespace: str) -> None:
        self._in_flight[namespace] += 1

    def note_call_finished(self, namespace: str) -> None:
        self._in_flight[namespace] = max(0, self._in_flight[namespace] - 1)

    async def shutdown(self) -> None:
        namespaces = list(self._runtimes.keys())
        for namespace in namespaces:
            await self.disconnect(namespace)

    async def _runtime_actor(
        self,
        record: ToolsetRecord,
        queue: asyncio.Queue[RuntimeCommand],
        bootstrap_future: asyncio.Future[RuntimeBootstrap],
    ) -> None:
        parameters = StdioServerParameters(
            command=record.transport.command,
            args=record.transport.args,
            env=record.transport.env or None,
            cwd=record.transport.cwd,
        )

        current_future: asyncio.Future[Any] | None = None
        stop_error: Exception | None = None
        try:
            async with AsyncExitStack() as exit_stack:
                read_stream, write_stream = await exit_stack.enter_async_context(stdio_client(parameters))
                session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))

                await session.initialize()
                tool_result = await session.list_tools()
                bootstrap = RuntimeBootstrap(
                    tools=list(tool_result.tools),
                    normalized_tools=validate_tool_inventory(record.namespace, tool_result.tools),
                )
                bootstrap_future.set_result(bootstrap)

                while True:
                    command = await queue.get()
                    current_future = command.future
                    if command.kind == "close":
                        if current_future is not None and not current_future.done():
                            current_future.set_result(True)
                        current_future = None
                        return
                    if command.kind == "probe":
                        try:
                            tool_result = await session.list_tools()
                            probe = RuntimeProbe(
                                tools=list(tool_result.tools),
                                normalized_tools=validate_tool_inventory(record.namespace, tool_result.tools),
                            )
                        except Exception as exc:
                            if current_future is not None and not current_future.done():
                                current_future.set_exception(exc)
                        else:
                            if current_future is not None and not current_future.done():
                                current_future.set_result(probe)
                        finally:
                            current_future = None
                        continue
                    if command.kind != "call_tool":
                        if current_future is not None and not current_future.done():
                            current_future.set_exception(RuntimeError(f"Unknown runtime command: {command.kind}"))
                        current_future = None
                        continue

                    try:
                        result = await session.call_tool(command.tool_name or "", command.arguments or {})
                    except Exception as exc:
                        if current_future is not None and not current_future.done():
                            current_future.set_exception(exc)
                    else:
                        if current_future is not None and not current_future.done():
                            current_future.set_result(result)
                    finally:
                        current_future = None
        except asyncio.CancelledError:
            stop_error = RuntimeError(f"Managed runtime for {record.namespace} was cancelled")
            if not bootstrap_future.done():
                bootstrap_future.set_exception(stop_error)
            raise
        except Exception as exc:
            stop_error = exc
            if not bootstrap_future.done():
                bootstrap_future.set_exception(exc)
            raise
        finally:
            if stop_error is None:
                stop_error = RuntimeError(f"Managed runtime for {record.namespace} stopped")
            if current_future is not None and not current_future.done():
                current_future.set_exception(stop_error)
            _fail_pending_commands(queue, stop_error)


def _fail_pending_commands(queue: asyncio.Queue[RuntimeCommand], exc: Exception) -> None:
    while True:
        try:
            command = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        if command.future is not None and not command.future.done():
            command.future.set_exception(exc)


async def _drain_task(task: asyncio.Task[None]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def _settle_runtime_callbacks() -> None:
    turns = 3 if sys.platform == "win32" else 1
    for _ in range(turns):
        await asyncio.sleep(0)

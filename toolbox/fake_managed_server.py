from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def build_server(manifest_path: Path) -> FastMCP:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tools = manifest.get("tools", [])
    server = FastMCP(
        name="FakeManagedToolset",
        instructions="A fake managed MCP server used by Toolbox integration tests.",
    )

    for definition in tools:
        name = definition["name"]
        title = definition.get("title")
        description = definition.get("description")
        response = definition.get("response", {})
        delay_seconds = float(definition.get("delay_seconds", 0))
        crash_process = bool(definition.get("crash_process", False))

        def make_tool(tool_name: str, payload: dict[str, Any], tool_delay_seconds: float, should_crash: bool) -> Any:
            def dynamic_tool(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                if tool_delay_seconds > 0:
                    time.sleep(tool_delay_seconds)
                if should_crash:
                    os._exit(1)
                return {
                    "tool": tool_name,
                    "arguments": arguments or {},
                    "response": payload,
                }

            dynamic_tool.__name__ = tool_name
            dynamic_tool.__doc__ = description or ""
            return dynamic_tool

        server.add_tool(
            make_tool(name, response, delay_seconds, crash_process),
            name=name,
            title=title,
            description=description,
            structured_output=True,
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    build_server(Path(args.manifest)).run("stdio")


if __name__ == "__main__":
    main()

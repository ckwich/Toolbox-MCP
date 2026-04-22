from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from mcp import types

from toolbox.models import RefreshResult, ToolSchema


def _sha256(payload: dict[str, Any] | list[Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def normalize_tool(tool: types.Tool) -> ToolSchema:
    payload = {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "input_schema": tool.inputSchema,
        "output_schema": tool.outputSchema,
    }
    return ToolSchema(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=tool.inputSchema or {},
        output_schema=tool.outputSchema,
        tool_hash=_sha256(payload),
    )


def validate_tool_inventory(namespace: str, tools: list[types.Tool]) -> list[ToolSchema]:
    names = [tool.name for tool in tools]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        duplicate_list = ", ".join(duplicates)
        raise ValueError(f"{namespace} returned duplicate tool names: {duplicate_list}")

    normalized = [normalize_tool(tool) for tool in tools]
    normalized.sort(key=lambda item: item.name)
    return normalized


def inventory_hash(tools: list[ToolSchema]) -> str:
    payload = [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "tool_hash": tool.tool_hash,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return _sha256(payload)


def diff_toolsets(namespace: str, previous: list[ToolSchema], current: list[ToolSchema]) -> RefreshResult:
    previous_by_name = {tool.name: tool for tool in previous}
    current_by_name = {tool.name: tool for tool in current}

    added = sorted(name for name in current_by_name if name not in previous_by_name)
    removed = sorted(name for name in previous_by_name if name not in current_by_name)
    changed = sorted(
        name
        for name in current_by_name
        if name in previous_by_name and current_by_name[name].tool_hash != previous_by_name[name].tool_hash
    )

    return RefreshResult(
        namespace=namespace,
        new_schema_hash=inventory_hash(current),
        added_tools=added,
        removed_tools=removed,
        changed_tools=changed,
    )

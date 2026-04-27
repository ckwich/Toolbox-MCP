from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from toolbox.skills_server import create_server


def parse_result_payload(result) -> dict:
    text_blocks = [block.text for block in result.content if hasattr(block, "text")]
    assert text_blocks
    return json.loads(text_blocks[0])


def write_skill(path: Path, *, name: str, description: str, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_skills_server_exposes_skill_loader_tools(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "skills" / "alpha" / "SKILL.md",
        name="alpha",
        description="User skill for alpha workflows.",
        body="Alpha body.",
    )
    server = create_server(skills_root=tmp_path / "skills", plugin_cache=tmp_path / "plugins")

    async with create_connected_server_and_client_session(server) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}

        assert {
            "skills_list",
            "skills_load",
            "skills_search",
            "skills_validate",
        }.issubset(tool_names)

        listing = await client.call_tool("skills_list", {})
        listing_payload = parse_result_payload(listing)
        assert listing_payload["error"] is None
        assert listing_payload["skills"][0]["name"] == "alpha"

        loaded = await client.call_tool("skills_load", {"name": "alpha"})
        loaded_payload = parse_result_payload(loaded)
        assert loaded_payload["error"] is None
        assert loaded_payload["skill"]["name"] == "alpha"
        assert "Alpha body." in loaded_payload["content"]


@pytest.mark.asyncio
async def test_skills_server_can_include_plugin_skills(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "plugins" / "cache" / "publisher" / "pack" / "1.0" / "skills" / "browser" / "SKILL.md",
        name="pack:browser",
        description="Plugin browser helpers.",
        body="Browser body.",
    )
    server = create_server(skills_root=tmp_path / "skills", plugin_cache=tmp_path / "plugins" / "cache")

    async with create_connected_server_and_client_session(server) as client:
        default_listing = await client.call_tool("skills_list", {})
        default_payload = parse_result_payload(default_listing)
        assert default_payload["count"] == 0

        plugin_listing = await client.call_tool("skills_list", {"include_plugins": True})
        plugin_payload = parse_result_payload(plugin_listing)
        assert plugin_payload["count"] == 1
        assert plugin_payload["skills"][0]["source"] == "plugin"

        search = await client.call_tool("skills_search", {"query": "browser", "include_plugins": True})
        search_payload = parse_result_payload(search)
        assert search_payload["results"][0]["name"] == "pack:browser"

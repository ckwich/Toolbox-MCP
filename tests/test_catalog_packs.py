from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from toolbox.service import ToolboxService


REPO_ROOT = Path(__file__).resolve().parents[1]
CODEX_SKILLS_PACK = REPO_ROOT / "docs" / "catalog-packs" / "codex-skills.json"
CATALOG_PACK_PATH = "docs/catalog-packs/codex-skills.json"
GODOT_TOOLS_PACK = REPO_ROOT / "docs" / "catalog-packs" / "ulana-godot-agent-tools.json"
GODOT_TOOLS_PACK_PATH = "docs/catalog-packs/ulana-godot-agent-tools.json"


@pytest.mark.asyncio
async def test_codex_skills_catalog_pack_is_agent_ready(tmp_path: Path) -> None:
    assert CODEX_SKILLS_PACK.is_file()

    workspace = tmp_path / "workspace"
    pack_target = workspace / CATALOG_PACK_PATH
    pack_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CODEX_SKILLS_PACK, pack_target)

    service = ToolboxService(state_path=workspace / ".toolbox" / "state.json")
    try:
        validation = service.validate_catalog_pack(CATALOG_PACK_PATH)

        assert validation["error"] is None
        assert validation["valid"] is True
        assert validation["pack"]["name"] == "codex-skills"
        assert validation["pack"]["toolset_count"] == 1

        summary = validation["toolsets"][0]
        assert summary["namespace"] == "codex_skills"
        assert summary["category"] == "skills"
        assert summary["aliases"]
        assert summary["auth_required"] is False

        dry_run = await service.import_catalog_pack(CATALOG_PACK_PATH, dry_run=True)
        assert dry_run["error"] is None
        assert dry_run["would_import"] == [{"namespace": "codex_skills", "action": "create"}]
        assert service.store.get_toolset("codex_skills") is None

        imported = await service.import_catalog_pack(CATALOG_PACK_PATH)
        assert imported["error"] is None
        assert imported["imported"][0]["namespace"] == "codex_skills"
        assert imported["imported"][0]["guidance_available"] is True

        default_payload = json.dumps(imported, sort_keys=True)
        assert "Start with skills_search" not in default_payload
        assert "payload" not in imported["imported"][0]["composition_examples"][0]

        quality = service.inspect_toolset_quality(["codex_skills"])
        assert quality["error"] is None
        codex_skills_quality = quality["quality"][0]
        assert codex_skills_quality["quality"]["grade"] == "excellent"
        assert codex_skills_quality["capability_flags"]["has_guidance"] is True
        assert codex_skills_quality["capability_flags"]["has_recipes"] is True
        assert codex_skills_quality["capability_flags"]["has_examples"] is True
        assert codex_skills_quality["capability_flags"]["has_future_task_metadata"] is True

        metadata_gaps = {
            "category",
            "aliases",
            "examples_or_recipes",
            "activation_hint",
            "cost_hint",
            "latency_hint",
            "trust_hint",
            "guidance",
        }
        assert metadata_gaps.isdisjoint(codex_skills_quality["quality"]["gaps"])

        guide = service.get_toolset_guide("codex_skills")
        assert guide["error"] is None
        assert guide["guidance_available"] is True
        assert guide["composition_examples"][0]["id"] == "search_then_load_skill"
        assert "Start with skills_search" not in json.dumps(guide, sort_keys=True)

        guidance = service.load_toolset_guidance("codex_skills")
        assert guidance["error"] is None
        assert guidance["guidance"][0]["title"] == "Skill Loading Guide"
        assert "Start with skills_search" in guidance["guidance"][0]["content"]
        assert "Use skills_load" in guidance["guidance"][0]["content"]

        examples = service.list_toolset_examples("codex_skills")
        assert examples["error"] is None
        assert examples["examples"][0]["id"] == "search_then_load_skill"
        assert "payload" not in examples["examples"][0]

        example = service.load_toolset_example("codex_skills", "search_then_load_skill")
        assert example["error"] is None
        assert example["example"]["payload"]["steps"][0]["tool"] == "codex_skills.skills_search"
        assert example["example"]["payload"]["steps"][1]["arguments"]["name"] == {
            "$from": "search.results.0.name"
        }
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_ulana_godot_catalog_pack_is_agent_ready(tmp_path: Path) -> None:
    assert GODOT_TOOLS_PACK.is_file()
    pack_payload = json.loads(GODOT_TOOLS_PACK.read_text(encoding="utf-8"))
    pack_text = json.dumps(pack_payload, sort_keys=True)
    assert "C:\\Dev" not in pack_text

    transport = pack_payload["toolsets"][0]["transport"]
    assert (
        transport["command"]
        == "/Users/ckwichman/.local/share/codex-migration/node-v24.14.0-darwin-arm64/bin/node"
    )
    assert transport["cwd"] == "/Users/ckwichman/Documents/Projects/godot_mcp"
    assert transport["args"] == [
        "/Users/ckwichman/Documents/Projects/godot_mcp/packages/mcp-server/build/src/index.js"
    ]
    assert transport["env"]["ULANA_GODOT_PATH"] == "/Applications/Godot.app/Contents/MacOS/Godot"

    workspace = tmp_path / "workspace"
    pack_target = workspace / GODOT_TOOLS_PACK_PATH
    pack_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GODOT_TOOLS_PACK, pack_target)

    service = ToolboxService(state_path=workspace / ".toolbox" / "state.json")
    try:
        validation = service.validate_catalog_pack(GODOT_TOOLS_PACK_PATH)

        assert validation["error"] is None
        assert validation["valid"] is True
        assert validation["pack"]["name"] == "ulana-godot-agent-tools"
        assert validation["pack"]["toolset_count"] == 1

        summary = validation["toolsets"][0]
        assert summary["namespace"] == "ulana_godot_agent_tools"
        assert summary["category"] == "game_dev"
        assert summary["aliases"]
        assert summary["auth_required"] is False

        dry_run = await service.import_catalog_pack(GODOT_TOOLS_PACK_PATH, dry_run=True)
        assert dry_run["error"] is None
        assert dry_run["would_import"] == [
            {"namespace": "ulana_godot_agent_tools", "action": "create"}
        ]
        assert service.store.get_toolset("ulana_godot_agent_tools") is None

        imported = await service.import_catalog_pack(GODOT_TOOLS_PACK_PATH)
        assert imported["error"] is None
        assert imported["imported"][0]["namespace"] == "ulana_godot_agent_tools"
        assert imported["imported"][0]["guidance_available"] is True

        default_payload = json.dumps(imported, sort_keys=True)
        assert "Use this toolset only when a task needs Godot" not in default_payload
        assert "payload" not in imported["imported"][0]["composition_examples"][0]

        quality = service.inspect_toolset_quality(["ulana_godot_agent_tools"])
        assert quality["error"] is None
        godot_quality = quality["quality"][0]
        assert godot_quality["quality"]["grade"] == "excellent"
        assert godot_quality["capability_flags"]["has_guidance"] is True
        assert godot_quality["capability_flags"]["has_recipes"] is True
        assert godot_quality["capability_flags"]["has_examples"] is True
        assert godot_quality["capability_flags"]["has_future_task_metadata"] is True

        guide = service.get_toolset_guide("ulana_godot_agent_tools")
        assert guide["error"] is None
        assert guide["guidance_available"] is True
        assert guide["composition_examples"][0]["id"] == "vector_overdrive_readiness_snapshot"

        guidance = service.load_toolset_guidance("ulana_godot_agent_tools")
        assert guidance["error"] is None
        assert guidance["guidance"][0]["title"] == "Godot Bridge Agent Guide"
        assert "editor_bridge_get_status" in guidance["guidance"][0]["content"]

        example = service.load_toolset_example(
            "ulana_godot_agent_tools",
            "vector_overdrive_readiness_snapshot",
        )
        assert example["error"] is None
        steps = example["example"]["payload"]["steps"]
        assert steps[0]["tool"] == "ulana_godot_agent_tools.godot_get_project_info"
        assert steps[1]["arguments"]["operation"] == "validate_project"
        assert steps[2]["tool"] == "ulana_godot_agent_tools.editor_bridge_get_status"
    finally:
        await service.shutdown()

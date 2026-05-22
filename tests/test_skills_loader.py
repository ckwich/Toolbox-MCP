from __future__ import annotations

import json
import os
from pathlib import Path

from toolbox.skills_loader import SkillRegistry


def write_skill(path: Path, *, name: str | None, description: str | None, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter: list[str] = ["---"]
    if name is not None:
        frontmatter.append(f"name: {name}")
    if description is not None:
        frontmatter.append(f"description: {description}")
    frontmatter.append("---")
    path.write_text("\n".join([*frontmatter, "", body]), encoding="utf-8")


def write_raw_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_list_skills_discovers_user_system_runtime_and_plugin_skills(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    plugin_cache = tmp_path / "plugins" / "cache"
    write_skill(
        skills_root / "alpha" / "SKILL.md",
        name="alpha",
        description="User skill for alpha workflows.",
    )
    write_skill(
        skills_root / ".system" / "sys-alpha" / "SKILL.md",
        name="sys-alpha",
        description="System skill.",
    )
    write_skill(
        skills_root / "codex-primary-runtime" / "documents" / "skills" / "docx" / "SKILL.md",
        name="docx",
        description="Runtime document skill.",
    )
    write_skill(
        plugin_cache / "openai-curated" / "browser-use" / "0.1.0" / "skills" / "browser" / "SKILL.md",
        name="browser-use:browser",
        description="Plugin browser skill.",
    )

    registry = SkillRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    default_listing = registry.list_skills()
    assert default_listing["error"] is None
    assert [skill["name"] for skill in default_listing["skills"]] == ["alpha"]

    full_listing = registry.list_skills(include_system=True, include_runtime=True, include_plugins=True)
    skills_by_name = {skill["name"]: skill for skill in full_listing["skills"]}

    assert skills_by_name["alpha"]["source"] == "user"
    assert skills_by_name["sys-alpha"]["source"] == "system"
    assert skills_by_name["docx"]["source"] == "runtime"
    assert skills_by_name["browser-use:browser"]["source"] == "plugin"
    assert all("content" not in skill for skill in full_listing["skills"])


def test_load_skill_returns_content_metadata_and_truncation(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    body = "Use this skill for alpha workflows.\n" + ("x" * 200)
    write_skill(
        skills_root / "alpha" / "SKILL.md",
        name="alpha",
        description="User skill for alpha workflows.",
        body=body,
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    loaded = registry.load_skill("alpha", max_chars=80)

    assert loaded["error"] is None
    assert loaded["skill"]["name"] == "alpha"
    assert loaded["skill"]["description"] == "User skill for alpha workflows."
    assert loaded["skill"]["source"] == "user"
    assert loaded["skill"]["sha256"].startswith("sha256:")
    assert loaded["content"].startswith("---\nname: alpha")
    assert len(loaded["content"]) == 80
    assert loaded["truncated"] is True
    assert loaded["content_chars"] > len(loaded["content"])


def test_search_skills_scores_name_description_and_body_matches(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    write_skill(
        skills_root / "alpha" / "SKILL.md",
        name="alpha",
        description="Manage Obsidian notes.",
        body="Vault operations and note lookup.",
    )
    write_skill(
        skills_root / "beta" / "SKILL.md",
        name="beta",
        description="Browser testing helpers.",
        body="Automate Chromium with Playwright.",
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    result = registry.search_skills("browser")

    assert result["error"] is None
    assert result["count"] == 1
    assert result["results"][0]["name"] == "beta"
    assert result["results"][0]["score"] > 0
    assert "Browser testing helpers" in result["results"][0]["snippet"]


def test_search_skills_scores_multi_term_queries(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    write_skill(
        skills_root / "risk-reward" / "SKILL.md",
        name="risk-reward-design",
        description="Review risk, reward, balance, pressure, and playtest signals.",
        body="Use for push-your-luck tuning.",
    )
    write_skill(
        skills_root / "audio" / "SKILL.md",
        name="audio-review",
        description="Review mix hierarchy.",
        body="Use for captions and hearing safety.",
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    result = registry.search_skills("risk reward balance playtest")

    assert result["error"] is None
    assert result["count"] == 1
    assert result["results"][0]["name"] == "risk-reward-design"
    assert "risk, reward, balance" in result["results"][0]["snippet"]


def test_search_skills_returns_match_reasons_and_ranked_snippets(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    write_skill(
        skills_root / "ui" / "SKILL.md",
        name="ui",
        description="Direct interface review.",
        body="General notes.",
    )
    write_skill(
        skills_root / "visual-review" / "SKILL.md",
        name="visual-review",
        description="UI critique for product surfaces.",
        body="General notes.",
    )
    write_skill(
        skills_root / "body-only" / "SKILL.md",
        name="body-only",
        description="General helper.",
        body="ui ui ui ui ui details live here.",
    )
    write_skill(
        skills_root / "mobile-qa" / "SKILL.md",
        name="mobile-qa",
        description="QA checks for iOS and 3D scenes.",
        body="Use for device validation.",
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    result = registry.search_skills("ui")

    assert result["error"] is None
    assert [item["name"] for item in result["results"][:3]] == ["ui", "visual-review", "body-only"]
    assert result["results"][0]["match_reasons"] == sorted(result["results"][0]["match_reasons"])
    assert set(result["results"][0]["match_reasons"]) == {
        "name_exact",
        "name_phrase",
        "name_term",
        "folder_exact",
        "folder_phrase",
        "folder_term",
    }
    assert result["results"][0]["matched_terms"] == ["ui"]
    assert set(result["results"][1]["match_reasons"]) == {"description_phrase", "description_term"}
    assert "UI critique" in result["results"][1]["snippet"]
    assert set(result["results"][2]["match_reasons"]) == {"body_phrase", "body_term"}
    assert "content" not in result["results"][2]

    short_terms = registry.search_skills("qa ios 3d")

    assert short_terms["error"] is None
    assert short_terms["results"][0]["name"] == "mobile-qa"
    assert short_terms["results"][0]["matched_terms"] == ["3d", "ios", "qa"]
    assert "description_term" in short_terms["results"][0]["match_reasons"]


def test_validate_skills_reports_missing_frontmatter_fields(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    write_skill(
        skills_root / "valid" / "SKILL.md",
        name="valid",
        description="Valid skill.",
    )
    write_skill(
        skills_root / "missing-description" / "SKILL.md",
        name="missing-description",
        description=None,
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    result = registry.validate_skills()

    assert result["error"] is None
    validations = {item["folder"]: item for item in result["validations"]}
    assert validations["valid"]["valid"] is True
    assert validations["missing-description"]["valid"] is False
    assert "missing description" in validations["missing-description"]["errors"]


def test_load_skill_can_return_specific_markdown_section(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    body = "\n".join(
        [
            "# Usage",
            "",
            "First usage block.",
            "",
            "```md",
            "# Ignored Heading",
            "```",
            "",
            "# Usage",
            "",
            "Second usage block.",
            "",
            "# Api Usage",
            "",
            "Third usage block.",
            "",
            "# API Usage",
            "",
            "Fourth usage block.",
            "",
            "# Notes",
            "",
            "Keep this separate.",
        ]
    )
    write_skill(skills_root / "alpha" / "SKILL.md", name="alpha", description="Sectioned skill.", body=body)
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=tmp_path / "plugins")

    full = registry.load_skill("alpha")
    usage = registry.load_skill("alpha", section="usage")
    second_usage = registry.load_skill("alpha", section="usage-2")
    missing = registry.load_skill("alpha", section="missing")
    ambiguous = registry.load_skill("alpha", section="API Usage")

    assert full["error"] is None
    assert [section["id"] for section in full["sections"]] == [
        "usage",
        "usage-2",
        "api-usage",
        "api-usage-2",
        "notes",
    ]
    assert "ignored-heading" not in {section["id"] for section in full["sections"]}
    assert usage["error"] is None
    assert usage["selected_section"]["id"] == "usage"
    assert usage["content"].startswith("# Usage")
    assert "First usage block." in usage["content"]
    assert "Second usage block." not in usage["content"]
    assert second_usage["selected_section"]["id"] == "usage-2"
    assert "Second usage block." in second_usage["content"]
    assert "full_content_chars" in second_usage
    assert second_usage["content_chars"] == len(second_usage["content"])
    assert missing["error"] == "section_not_found"
    assert [section["id"] for section in missing["sections"]] == [section["id"] for section in full["sections"]]
    assert ambiguous["error"] == "ambiguous_section"
    assert {section["id"] for section in ambiguous["matches"]} == {"api-usage", "api-usage-2"}


def test_load_skill_rejects_unknown_and_ambiguous_names(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    plugin_cache = tmp_path / "plugins"
    write_skill(skills_root / "alpha" / "SKILL.md", name="duplicate", description="User copy.")
    write_skill(
        plugin_cache / "publisher" / "pack" / "1.0" / "skills" / "duplicate" / "SKILL.md",
        name="duplicate",
        description="Plugin copy.",
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    unknown = registry.load_skill("missing")
    ambiguous = registry.load_skill("duplicate", include_plugins=True)
    resolved = registry.load_skill("duplicate", source="user", include_plugins=True)

    assert unknown["error"] == "skill_not_found"
    assert ambiguous["error"] == "ambiguous_skill_name"
    assert {match["source"] for match in ambiguous["matches"]} == {"user", "plugin"}
    assert resolved["error"] is None
    assert resolved["skill"]["source"] == "user"


def test_roots_status_reports_source_counts_missing_roots_and_duplicates(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    plugin_cache = tmp_path / "missing-plugins"
    write_skill(skills_root / "alpha-a" / "SKILL.md", name="duplicate", description="First copy.")
    write_skill(skills_root / "alpha-b" / "SKILL.md", name="duplicate", description="Second copy.")
    write_skill(skills_root / ".system" / "sys" / "SKILL.md", name="sys", description="System skill.")
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    status = registry.roots_status(include_system=True, include_runtime=True, include_plugins=True)

    assert status["error"] is None
    assert status["enabled_sources"] == {
        "user": True,
        "system": True,
        "runtime": True,
        "plugin": True,
    }
    assert status["source_flags"] == status["enabled_sources"]
    assert status["roots"]["skills_root"]["path"] == str(skills_root)
    assert status["roots"]["skills_root"]["source"] == "user"
    assert status["roots"]["skills_root"]["exists"] is True
    assert status["roots"]["skills_root"]["skill_count"] == 2
    assert status["roots"]["runtime_root"]["exists"] is False
    assert status["roots"]["runtime_root"]["skill_count"] == 0
    assert status["roots"]["runtime_root"]["errors"][0]["code"] == "missing_root"
    assert status["roots"]["plugin_cache"]["path"] == str(plugin_cache)
    assert status["roots"]["plugin_cache"]["exists"] is False
    assert status["roots"]["plugin_cache"]["scan_skipped_reason"] is None
    assert status["counts_by_source"] == {"user": 2, "system": 1, "runtime": 0, "plugin": 0}
    assert status["skill_md_counts"]["total"] == 3
    assert status["total_skill_count"] == 3
    assert status["duplicate_name_groups"][0]["name"] == "duplicate"
    assert status["duplicate_name_groups"][0]["normalized_name"] == "duplicate"
    assert status["duplicate_name_groups"][0]["count"] == 2
    assert {record["folder"] for record in status["duplicate_name_groups"][0]["records"]} == {
        "alpha-a",
        "alpha-b",
    }
    serialized = json.dumps(status)
    assert '"content"' not in serialized
    assert '"body"' not in serialized
    assert '"frontmatter"' not in serialized


def test_roots_status_reports_lookup_collisions_and_overlapping_roots(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    plugin_cache = skills_root / "plugins"
    write_skill(skills_root / "alias-folder" / "SKILL.md", name="canonical", description="Folder alias.")
    write_skill(skills_root / "other" / "SKILL.md", name="alias-folder", description="Name alias.")
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    status = registry.roots_status()

    assert status["roots"]["plugin_cache"]["enabled"] is False
    assert status["roots"]["plugin_cache"]["skill_count"] is None
    assert status["roots"]["plugin_cache"]["scan_skipped_reason"] == "source_excluded"
    assert status["ambiguous_lookup_groups"][0]["lookup_key"] == "alias-folder"
    assert status["ambiguous_lookup_groups"][0]["count"] == 2
    assert {skill["folder"] for skill in status["ambiguous_lookup_groups"][0]["skills"]} == {
        "alias-folder",
        "other",
    }
    assert status["warnings"][0]["code"] == "overlapping_roots"


def test_validate_skills_reports_structured_findings_and_source_filtering(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    plugin_cache = tmp_path / "plugins"
    write_skill(skills_root / "valid" / "SKILL.md", name="valid", description="Valid skill.")
    write_skill(skills_root / "missing-description" / "SKILL.md", name="missing-description", description=None)
    write_skill(skills_root / "empty-description" / "SKILL.md", name="empty-description", description="")
    write_skill(skills_root / "long-description" / "SKILL.md", name="long-description", description="x" * 260)
    write_raw_skill(
        skills_root / "malformed" / "SKILL.md",
        "---\nname: malformed\ndescription: never closes\n\nBody starts without closing frontmatter.",
    )
    write_skill(skills_root / "user-dupe" / "SKILL.md", name="shared", description="User copy.")
    write_skill(
        plugin_cache / "publisher" / "pack" / "1.0" / "skills" / "plugin-dupe-a" / "SKILL.md",
        name="plugin-only",
        description="Plugin copy one.",
    )
    write_skill(
        plugin_cache / "publisher" / "pack" / "1.0" / "skills" / "plugin-dupe-b" / "SKILL.md",
        name="plugin-only",
        description="Plugin copy two.",
    )
    write_skill(
        plugin_cache / "publisher" / "pack" / "1.0" / "skills" / "shared" / "SKILL.md",
        name="shared",
        description="Plugin shared copy.",
    )
    registry = SkillRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    result = registry.validate_skills(include_plugins=True)
    plugin_result = registry.validate_skills(source="plugin")

    assert result["error"] is None
    validations = {item["folder"]: item for item in result["validations"]}
    assert "frontmatter_keys" in validations["valid"]
    assert validations["missing-description"]["valid"] is False
    assert validations["empty-description"]["valid"] is False
    assert any(finding["code"] == "missing_description" for finding in validations["empty-description"]["findings"])
    assert validations["long-description"]["valid"] is True
    assert any(finding["code"] == "long_description" for finding in validations["long-description"]["findings"])
    assert validations["malformed"]["valid"] is False
    assert any(finding["code"] == "malformed_frontmatter" for finding in validations["malformed"]["findings"])

    aggregate_findings = {(finding["code"], finding["severity"], finding.get("duplicate_scope")) for finding in result["findings"]}
    assert ("duplicate_name", "error", "same_source") in aggregate_findings
    assert ("duplicate_name", "warning", "cross_source") in aggregate_findings
    assert result["valid"] is False
    assert plugin_result["error"] is None
    assert plugin_result["count"] == 3
    assert {item["source"] for item in plugin_result["validations"]} == {"plugin"}


def test_discovery_uses_cache_until_skill_files_change(tmp_path: Path) -> None:
    class CountingRegistry(SkillRegistry):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.read_count = 0
            self.iter_roots: list[Path] = []

        def _read_record(self, path: Path, source: str):  # type: ignore[no-untyped-def]
            self.read_count += 1
            return super()._read_record(path, source)

        def _iter_skill_paths(self, root: Path) -> list[Path]:
            self.iter_roots.append(root)
            return super()._iter_skill_paths(root)

    skills_root = tmp_path / "skills"
    plugin_cache = tmp_path / "plugins"
    alpha_path = skills_root / "alpha" / "SKILL.md"
    beta_path = skills_root / "beta" / "SKILL.md"
    write_skill(alpha_path, name="alpha", description="Alpha skill.", body="Alpha body.")
    write_skill(
        plugin_cache / "publisher" / "pack" / "1.0" / "skills" / "plugin" / "SKILL.md",
        name="plugin",
        description="Plugin skill.",
    )
    registry = CountingRegistry(skills_root=skills_root, plugin_cache=plugin_cache)

    listing = registry.list_skills()
    listing["skills"][0]["errors"].append("poison")
    registry.search_skills("alpha")
    loaded = registry.load_skill("alpha", include_system=False, include_runtime=False)
    loaded["frontmatter"]["name"] = "poison"
    loaded_again = registry.load_skill("alpha", include_system=False, include_runtime=False)

    assert registry.read_count == 1
    assert plugin_cache not in registry.iter_roots
    assert "poison" not in registry.list_skills()["skills"][0]["errors"]
    assert loaded_again["frontmatter"]["name"] == "alpha"

    before = alpha_path.stat().st_mtime_ns
    write_skill(alpha_path, name="alpha", description="Alpha skill changed.", body="Alpha body changed.")
    os.utime(alpha_path, ns=(before + 1_000_000, before + 1_000_000))
    changed = registry.list_skills()

    assert registry.read_count == 2
    assert changed["skills"][0]["description"] == "Alpha skill changed."

    write_skill(beta_path, name="beta", description="Beta skill.")
    added = registry.list_skills()
    assert registry.read_count == 4
    assert {skill["name"] for skill in added["skills"]} == {"alpha", "beta"}

    beta_path.unlink()
    deleted = registry.list_skills()
    assert registry.read_count == 5
    assert [skill["name"] for skill in deleted["skills"]] == ["alpha"]

    registry.list_skills(include_plugins=True)
    assert plugin_cache in registry.iter_roots

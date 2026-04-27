from __future__ import annotations

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

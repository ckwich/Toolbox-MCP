# Skill Loader Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the read-only Codex skill loader with better discovery, targeted loading, diagnostics, validation, and repeat-call performance without expanding it into a skill editor or installer.

**Architecture:** Keep `toolbox.skills_server` as the MCP boundary and `SkillRegistry` as the public Python API. Add small internal helpers to parse sections, rank matches with explicit reasons, compute root diagnostics, validate records across an inventory, and cache discovered records behind a root-signature check.

**Tech Stack:** Python 3.12, dataclasses, pathlib, FastMCP, pytest.

---

## Steelman

- The loader's best property is safety: read-only filesystem access, no arbitrary path loading, no script execution, and opt-in system/runtime/plugin roots. All five features must preserve that boundary.
- Search should become more explainable, not "AI-ish." Deterministic match reasons and snippets are more useful to agents than opaque fuzzy scores.
- Section loading should be additive. Existing `skills_load(name, max_chars=...)` callers must keep working, while callers that need less prompt load can request an ATX heading section by stable id or heading text.
- Root diagnostics should expose health, source coverage, and duplication without leaking full skill bodies or turning into a filesystem browser.
- Validation should help authors fix skills, but it should not reject normal working skills with fussy YAML rules. Parse simple frontmatter, distinguish absent and unclosed frontmatter, report malformed structures, and keep severity explicit.
- Caching must stay correct before it is clever. A cheap signature over discovered `SKILL.md` paths plus `st_mtime_ns` and sizes is enough; if a file is added, removed, renamed, or stat-changed, rebuild.
- Source filtering must remain explicit. Default calls only inspect user skills unless the caller opts into system, runtime, plugin, or names a source that requires one of those roots.

## Task 1: Explainable Search Ranking

**Files:**
- Modify: `toolbox/skills_loader.py`
- Test: `tests/test_skills_loader.py`

- [x] **Step 1: Write failing tests**
  - Add `test_search_skills_returns_match_reasons_and_ranked_snippets`.
  - Assert exact name or folder matches beat description matches, and description matches beat body-only matches even when the body repeats the query.
  - Assert each result includes `match_reasons`, `matched_terms`, and a relevant `snippet`.
  - Assert short meaningful terms such as `ui`, `qa`, `ios`, and `3d` can participate in matching.
  - Run: `.venv/bin/python -m pytest tests/test_skills_loader.py::test_search_skills_returns_match_reasons_and_ranked_snippets -q`
  - Expected: FAIL because search results lack reasons/terms and ranking is too simple.

- [x] **Step 2: Implement deterministic ranking**
  - Keep `_score_record` pure and deterministic.
  - Score phrase and term matches separately for `name`, `folder`, `description`, and `body`.
  - Normalize case, hyphens, and underscores; cap field contributions so repeated body terms cannot outrank a direct name/folder match.
  - Return score metadata to `search_skills`, including sorted unique `match_reasons` and `matched_terms`.
  - Preserve the existing result envelope and `score`/`snippet` fields; do not expose full skill bodies in search results.

- [x] **Step 3: Verify**
  - Run the new test and `tests/test_skills_loader.py -q`.

## Task 2: Section-Aware Loading

**Files:**
- Modify: `toolbox/skills_loader.py`
- Modify: `toolbox/skills_server.py`
- Test: `tests/test_skills_loader.py`
- Test: `tests/test_skills_server.py`

- [x] **Step 1: Write failing tests**
  - Add `test_load_skill_can_return_specific_markdown_section`.
  - Add server coverage for `skills_load` with a `section` argument.
  - Assert `skills_load("alpha")` returns a compact `sections` list.
  - Assert `skills_load("alpha", section="usage")` returns only the matching heading subtree, including the heading line, and reports `selected_section`.
  - Assert headings inside fenced code blocks are ignored and duplicate heading ids receive stable suffixes like `usage-2`.
  - Run the two new tests.
  - Expected: FAIL because `section` is not supported.

- [x] **Step 2: Implement section indexing**
  - Parse markdown headings from the skill body into stable ids: lowercase, alphanumeric words joined by hyphens.
  - Include `title`, `id`, `level`, `start_line`, and `end_line` in section summaries.
  - Add optional `section: str | None = None` to `SkillRegistry.load_skill` and `skills_load`.
  - Keep default loads backward-compatible: full file content, existing `content_chars`/`truncated`, plus additive `sections`.
  - For section loads, set `content_chars` to selected payload length and include `full_content_chars` for the full file length.
  - If `section` is not found, return `{"error": "section_not_found", ...}` with available sections.
  - Resolve errors in this order: invalid source, missing/ambiguous skill, then missing/ambiguous section.

- [x] **Step 3: Verify**
  - Run section loader tests plus existing skill server tests.

## Task 3: Root And Source Diagnostics

**Files:**
- Modify: `toolbox/skills_loader.py`
- Modify: `toolbox/skills_server.py`
- Modify: `docs/skill-loader-mcp.md`
- Test: `tests/test_skills_loader.py`
- Test: `tests/test_skills_server.py`

- [x] **Step 1: Write failing tests**
  - Add `test_roots_status_reports_source_counts_missing_roots_and_duplicates`.
  - Add server coverage that `skills_roots_status` is exposed.
  - Assert diagnostics include root paths, existence/readability flags, enabled source flags, counts by source, duplicate name groups, total skill count, and `SKILL.md` discovery counts.
  - Assert missing roots are reported as `exists: false` and do not raise.
  - Assert folder/name lookup collisions and overlapping configured roots are surfaced as diagnostics.
  - Assert diagnostics never include `content`, `body`, or full frontmatter values.
  - Expected: FAIL because no diagnostics API exists.

- [x] **Step 2: Implement diagnostics**
  - Add `SkillRegistry.roots_status(...)`.
  - Add MCP tool `skills_roots_status`.
  - Reuse the same source flags as listing/searching so defaults only cover user skills.
  - Report duplicate groups by normalized name with compact record summaries and paths.
  - Report ambiguous lookup groups and overlapping root warnings without scanning excluded sources.
  - Do not include full content in diagnostics.

- [x] **Step 3: Verify**
  - Run targeted loader/server tests.

## Task 4: Stronger Validation

**Files:**
- Modify: `toolbox/skills_loader.py`
- Modify: `docs/skill-loader-mcp.md`
- Test: `tests/test_skills_loader.py`

- [x] **Step 1: Write failing tests**
  - Add validation cases for malformed frontmatter without a closing `---`, duplicate names across enabled sources, empty descriptions, and very long descriptions.
  - Assert validations preserve existing `valid`, `errors`, and `frontmatter_keys` shape while adding structured `findings`.
  - Assert validations include `severity` values and root-level duplicate-name findings.
  - Assert `validate_skills(source="plugin")` filters to plugin records even when no name is supplied.
  - Expected: FAIL because current validation only reports per-record missing fields.

- [x] **Step 2: Implement validation findings**
  - Track frontmatter parse errors in each `SkillRecord`.
  - Keep `errors: list[str]` backward-compatible, and add structured findings with `code`, `severity`, `message`, and compact affected records.
  - Add `severity` to validation findings: `error` for unreadable/missing required fields/malformed frontmatter/same-source duplicate names, `warning` for long descriptions and cross-source duplicates.
  - Include aggregate `findings` in `validate_skills`.

- [x] **Step 3: Verify**
  - Run `tests/test_skills_loader.py -q`.

## Task 5: Cached Inventory

**Files:**
- Modify: `toolbox/skills_loader.py`
- Test: `tests/test_skills_loader.py`

- [x] **Step 1: Write failing tests**
  - Add `test_discovery_uses_cache_until_skill_files_change`.
  - Instrument a registry subclass or monkeypatch `_read_record` to count reads.
  - Assert repeated list/search calls reuse cached records, then modifying a skill invalidates the cache.
  - Assert adding/deleting a `SKILL.md` invalidates the cache and default user-only discovery does not stat plugin cache.
  - Assert mutating returned nested payload dictionaries/lists cannot poison cached records.
  - Expected: FAIL because every call walks and reads the filesystem.

- [x] **Step 2: Implement cache**
  - Add an in-process cache on `SkillRegistry`.
  - Cache by `(include_system, include_runtime, include_plugins)` plus a root signature of `SKILL.md` paths, `st_mtime_ns`, and sizes.
  - Add a small lock around cache check/update.
  - Return fresh result payloads and copy nested mutable values so callers cannot mutate cached state.

- [x] **Step 3: Verify**
  - Run loader tests.

## Integration Tasks

- [x] Update `docs/catalog-packs/codex-skills.json` guidance to mention section loading, root diagnostics, and match reasons.
- [x] Run `.venv/bin/python -m pytest tests/test_skills_loader.py tests/test_skills_server.py tests/test_skills_server_entrypoint.py tests/test_catalog_packs.py -q`.
- [x] Run `.venv/bin/python -m pytest -q`.
- [x] Run `.venv/bin/python -m compileall toolbox tests`.
- [x] Run `git diff --check`.
- [x] Commit one readable session commit.

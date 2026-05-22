from __future__ import annotations

import hashlib
import os
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_SOURCES = {"user", "system", "runtime", "plugin"}
DEFAULT_MAX_CHARS = 20_000
MAX_CONTENT_CHARS = 100_000
LONG_DESCRIPTION_CHARS = 240
MEANINGFUL_SHORT_TERMS = {"2d", "3d", "ai", "qa", "ui", "ux", "vr", "ar"}
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_ATX_HEADING_PATTERN = re.compile(r"^(?P<indent> {0,3})(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*)|[ \t]*)$")
_FENCE_PATTERN = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})")


@dataclass(frozen=True)
class ScoreResult:
    score: int
    match_reasons: tuple[str, ...]
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class SkillRecord:
    path: Path
    folder: str
    source: str
    name: str
    description: str
    frontmatter: dict[str, str]
    content: str
    body: str
    sha256: str
    modified_at: str | None
    errors: list[str]
    frontmatter_errors: list[str]

    @property
    def valid(self) -> bool:
        return not self.errors

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "folder": self.folder,
            "source": self.source,
            "path": str(self.path),
            "valid": self.valid,
            "errors": list(self.errors),
            "sha256": self.sha256,
            "modified_at": self.modified_at,
        }


def default_skills_root() -> Path:
    configured = os.environ.get("CODEX_SKILLS_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / ".codex" / "skills"


def default_plugin_cache() -> Path:
    configured = os.environ.get("CODEX_PLUGIN_CACHE")
    if configured:
        return Path(configured)
    return Path.home() / ".codex" / "plugins" / "cache"


class SkillRegistry:
    def __init__(
        self,
        skills_root: Path | str | None = None,
        plugin_cache: Path | str | None = None,
    ) -> None:
        self.skills_root = Path(skills_root) if skills_root is not None else default_skills_root()
        self.plugin_cache = Path(plugin_cache) if plugin_cache is not None else default_plugin_cache()
        self._cache_lock = threading.RLock()
        self._records_cache: dict[
            tuple[bool, bool, bool],
            tuple[tuple[tuple[str, str, int | None, int | None], ...], list[SkillRecord]],
        ] = {}

    def list_skills(
        self,
        *,
        query: str | None = None,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, Any]:
        records = self._discover_records(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        filtered = self._filter_records(records, query)
        return {
            "error": None,
            "count": len(filtered),
            "skills_root": str(self.skills_root),
            "plugin_cache": str(self.plugin_cache),
            "skills": [record.summary() for record in filtered],
        }

    def load_skill(
        self,
        name: str,
        *,
        source: str | None = None,
        include_system: bool = True,
        include_runtime: bool = True,
        include_plugins: bool = False,
        max_chars: int = DEFAULT_MAX_CHARS,
        section: str | None = None,
    ) -> dict[str, Any]:
        matches_or_error = self._resolve_records(
            name,
            source=source,
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        if isinstance(matches_or_error, dict):
            return matches_or_error

        record = matches_or_error[0]
        safe_max = _normalize_max_chars(max_chars)
        sections = _parse_sections(record.body)
        section_summaries = [_section_summary(item) for item in sections]
        selected_section: dict[str, Any] | None = None
        full_content_chars = len(record.content)
        payload_content = record.content

        if section is not None:
            section_match = _resolve_section(section, sections)
            if section_match.get("error") is not None:
                return {
                    **section_match,
                    "skill": record.summary(),
                    "sections": section_summaries,
                    "available_sections": section_summaries,
                }
            selected = section_match
            selected_section = _section_summary(selected)
            payload_content = selected["content"]

        content = payload_content[:safe_max]
        result = {
            "error": None,
            "skill": record.summary(),
            "frontmatter": dict(record.frontmatter),
            "content": content,
            "content_chars": len(payload_content),
            "truncated": len(payload_content) > len(content),
            "sections": section_summaries,
        }
        if selected_section is not None:
            result["selected_section"] = selected_section
            result["full_content_chars"] = full_content_chars
        return result

    def search_skills(
        self,
        query: str,
        *,
        limit: int = 10,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, Any]:
        normalized_query = _normalize_search_text(query)
        if not normalized_query:
            return {"error": "empty_query", "count": 0, "results": []}

        records = self._discover_records(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        scored = [
            (score_result, record)
            for record in records
            if (score_result := _score_record(record, query)).score > 0
        ]
        scored.sort(key=lambda item: (-item[0].score, item[1].name, str(item[1].path)))
        safe_limit = max(1, min(limit, 100))
        results = []
        for score_result, record in scored[:safe_limit]:
            summary = record.summary()
            summary["score"] = score_result.score
            summary["match_reasons"] = list(score_result.match_reasons)
            summary["matched_terms"] = list(score_result.matched_terms)
            summary["snippet"] = _build_snippet(record, normalized_query)
            results.append(summary)

        return {"error": None, "count": len(results), "results": results}

    def validate_skills(
        self,
        name: str | None = None,
        *,
        source: str | None = None,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, Any]:
        if source is not None and source not in VALID_SOURCES:
            return {"error": "invalid_source", "valid_sources": sorted(VALID_SOURCES)}

        if name is not None:
            matches_or_error = self._resolve_records(
                name,
                source=source,
                include_system=include_system,
                include_runtime=include_runtime,
                include_plugins=include_plugins,
            )
            if isinstance(matches_or_error, dict):
                return matches_or_error
            records = matches_or_error
        else:
            records = self._discover_records(
                include_system=include_system or source == "system",
                include_runtime=include_runtime or source == "runtime",
                include_plugins=include_plugins or source == "plugin",
            )
            if source is not None:
                records = [record for record in records if record.source == source]

        aggregate_findings = _duplicate_name_findings(records)
        duplicate_error_paths = {
            affected["path"]
            for finding in aggregate_findings
            if finding["severity"] == "error"
            for affected in finding["affected_records"]
        }
        validations = []
        for record in records:
            item_findings = _record_validation_findings(record)
            item_errors = list(record.errors)
            if str(record.path) in duplicate_error_paths:
                item_errors.append("duplicate name in source")
                item_findings.append(
                    _finding(
                        "duplicate_name",
                        "error",
                        f"Duplicate skill name '{record.name}' in source '{record.source}'.",
                        affected_records=[_compact_record(record)],
                        duplicate_scope="same_source",
                    )
                )
            validations.append(
                {
                    **record.summary(),
                    "errors": item_errors,
                    "valid": not item_errors,
                    "frontmatter_keys": sorted(record.frontmatter),
                    "findings": item_findings,
                }
            )
        all_findings = [finding for item in validations for finding in item["findings"]]
        all_findings.extend(aggregate_findings)
        return {
            "error": None,
            "count": len(validations),
            "valid": all(item["valid"] for item in validations)
            and not any(finding["severity"] == "error" for finding in aggregate_findings),
            "validations": validations,
            "findings": all_findings,
        }

    def roots_status(
        self,
        *,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, Any]:
        records = self._discover_records(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        counts_by_source = {source: 0 for source in sorted(VALID_SOURCES)}
        for record in records:
            counts_by_source[record.source] += 1

        roots = {
            "skills_root": _root_status(self.skills_root, enabled=True),
            "system_root": _root_status(self.skills_root / ".system", enabled=include_system),
            "runtime_root": _root_status(self.skills_root / "codex-primary-runtime", enabled=include_runtime),
            "plugin_cache": _root_status(self.plugin_cache, enabled=include_plugins),
        }
        duplicate_groups = [
            {
                "name": normalized_name,
                "records": [_compact_record(record) for record in sorted(group, key=_record_sort_key)],
            }
            for normalized_name, group in _records_by_normalized_name(records).items()
            if len(group) > 1
        ]
        duplicate_groups.sort(key=lambda item: item["name"])

        return {
            "error": None,
            "skills_root": str(self.skills_root),
            "plugin_cache": str(self.plugin_cache),
            "enabled_sources": {
                "user": True,
                "system": include_system,
                "runtime": include_runtime,
                "plugin": include_plugins,
            },
            "roots": roots,
            "counts_by_source": counts_by_source,
            "skill_md_counts": {
                "total": len(records),
                "by_source": dict(counts_by_source),
            },
            "total_skill_count": len(records),
            "duplicate_name_groups": duplicate_groups,
        }

    def _resolve_records(
        self,
        name: str,
        *,
        source: str | None,
        include_system: bool,
        include_runtime: bool,
        include_plugins: bool,
    ) -> list[SkillRecord] | dict[str, Any]:
        if source is not None and source not in VALID_SOURCES:
            return {"error": "invalid_source", "valid_sources": sorted(VALID_SOURCES)}

        records = self._discover_records(
            include_system=include_system or source == "system",
            include_runtime=include_runtime or source == "runtime",
            include_plugins=include_plugins or source == "plugin",
        )
        normalized_name = name.strip().lower()
        matches = [
            record
            for record in records
            if record.name.lower() == normalized_name or record.folder.lower() == normalized_name
        ]
        if source is not None:
            matches = [record for record in matches if record.source == source]

        if not matches:
            return {"error": "skill_not_found", "name": name, "source": source}
        if len(matches) > 1:
            return {
                "error": "ambiguous_skill_name",
                "name": name,
                "matches": [record.summary() for record in sorted(matches, key=_record_sort_key)],
            }
        return matches

    def _discover_records(
        self,
        *,
        include_system: bool,
        include_runtime: bool,
        include_plugins: bool,
    ) -> list[SkillRecord]:
        cache_key = (include_system, include_runtime, include_plugins)
        paths_with_sources = self._eligible_skill_paths(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        signature = _paths_signature(paths_with_sources)
        with self._cache_lock:
            cached = self._records_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return list(cached[1])

        records: list[SkillRecord] = []
        seen: set[Path] = set()

        for path, source in paths_with_sources:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            records.append(self._read_record(path, source))
        records = sorted(records, key=_record_sort_key)
        with self._cache_lock:
            self._records_cache[cache_key] = (signature, records)
        return list(records)

    def _eligible_skill_paths(
        self,
        *,
        include_system: bool,
        include_runtime: bool,
        include_plugins: bool,
    ) -> list[tuple[Path, str]]:
        paths: list[tuple[Path, str]] = []
        paths.extend((path, "user") for path in self._iter_user_skill_paths())
        if include_system:
            paths.extend((path, "system") for path in self._iter_skill_paths(self.skills_root / ".system"))
        if include_runtime:
            paths.extend((path, "runtime") for path in self._iter_skill_paths(self.skills_root / "codex-primary-runtime"))

        if include_plugins:
            for path in self._iter_skill_paths(self.plugin_cache):
                if not _is_relative_to(path, self.plugin_cache):
                    continue
                paths.append((path, "plugin"))
        return paths

    def _iter_skill_paths(self, root: Path) -> list[Path]:
        if not root.exists() or not root.is_dir():
            return []
        return sorted(path for path in root.rglob("SKILL.md") if path.is_file())

    def _iter_user_skill_paths(self) -> list[Path]:
        if not self.skills_root.exists() or not self.skills_root.is_dir():
            return []

        paths: list[Path] = []
        root_skill = self.skills_root / "SKILL.md"
        if root_skill.is_file():
            paths.append(root_skill)

        for child in sorted(self.skills_root.iterdir()):
            if child.name in {".system", "codex-primary-runtime", "SKILL.md"}:
                continue
            if child.is_dir():
                paths.extend(path for path in child.rglob("SKILL.md") if path.is_file())
        return sorted(paths)

    def _read_record(self, path: Path, source: str) -> SkillRecord:
        errors: list[str] = []
        content = ""
        frontmatter: dict[str, str] = {}
        body = ""
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, body, frontmatter_errors = _parse_frontmatter(content)
            errors.extend(frontmatter_errors)
        except (OSError, UnicodeDecodeError) as exc:
            frontmatter_errors = []
            errors.append(f"read error: {exc}")

        folder = path.parent.name
        name = frontmatter.get("name", folder).strip()
        description = frontmatter.get("description", "").strip()
        if "name" not in frontmatter or not name:
            errors.append("missing name")
            name = folder
        if "description" not in frontmatter:
            errors.append("missing description")
        elif not description:
            errors.append("empty description")

        return SkillRecord(
            path=path,
            folder=folder,
            source=source,
            name=name,
            description=description,
            frontmatter=frontmatter,
            content=content,
            body=body,
            sha256=f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            modified_at=_modified_at(path),
            errors=errors,
            frontmatter_errors=frontmatter_errors,
        )

    def _filter_records(self, records: list[SkillRecord], query: str | None) -> list[SkillRecord]:
        if not query:
            return records
        normalized_query = query.strip().lower()
        if not normalized_query:
            return records
        return [
            record
            for record in records
            if normalized_query in record.name.lower()
            or normalized_query in record.folder.lower()
            or normalized_query in record.description.lower()
        ]


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str, list[str]]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content, []

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}, content, ["malformed frontmatter: missing closing ---"]

    frontmatter: dict[str, str] = {}
    errors: list[str] = []
    for line in lines[1:closing_index]:
        if not line.strip():
            continue
        if ":" not in line:
            errors.append(f"malformed frontmatter line: {line.strip()}")
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            errors.append(f"malformed frontmatter line: {line.strip()}")
            continue
        frontmatter[key] = value.strip().strip("\"'")

    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return frontmatter, body, errors


def _normalize_max_chars(max_chars: int) -> int:
    if max_chars < 1:
        return DEFAULT_MAX_CHARS
    return min(max_chars, MAX_CONTENT_CHARS)


def _record_sort_key(record: SkillRecord) -> tuple[str, str, str]:
    return (record.name.lower(), record.source, str(record.path).lower())


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _modified_at(path: Path) -> str | None:
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, timezone.utc).isoformat()


def _score_record(record: SkillRecord, query: str) -> ScoreResult:
    phrase = _normalize_search_text(query)
    terms = _query_terms(query)
    if not phrase and not terms:
        return ScoreResult(score=0, match_reasons=(), matched_terms=())

    fields = {
        "name": _field_match_data(record.name),
        "folder": _field_match_data(record.folder),
        "description": _field_match_data(record.description),
        "body": _field_match_data(record.body),
    }
    score = 0
    reasons: set[str] = set()
    matched_terms: set[str] = set()

    exact_weights = {"name": 120, "folder": 110}
    phrase_weights = {"name": 80, "folder": 75, "description": 45, "body": 15}
    term_weights = {"name": 12, "folder": 10, "description": 7, "body": 2}
    term_caps = {"name": 36, "folder": 30, "description": 35, "body": 10}

    for field, data in fields.items():
        field_text, field_terms = data
        if field in exact_weights and phrase and field_text == phrase:
            score += exact_weights[field]
            reasons.add(f"{field}_exact")
            matched_terms.update(terms or [phrase])

        if phrase and _phrase_matches(field_text, field_terms, phrase):
            score += phrase_weights[field]
            reasons.add(f"{field}_phrase")
            matched_terms.update(terms or [phrase])

        field_term_score = 0
        for term in terms:
            if term in field_terms:
                field_term_score += term_weights[field]
                reasons.add(f"{field}_term")
                matched_terms.add(term)
        score += min(field_term_score, term_caps[field])

    return ScoreResult(
        score=score,
        match_reasons=tuple(sorted(reasons)),
        matched_terms=tuple(sorted(matched_terms)),
    )


def _build_snippet(record: SkillRecord, query: str) -> str:
    phrase = _normalize_search_text(query)
    description_text, description_terms = _field_match_data(record.description)
    name_text, name_terms = _field_match_data(record.name)
    if phrase and _phrase_matches(description_text, description_terms, phrase):
        return record.description
    if phrase and _phrase_matches(name_text, name_terms, phrase) and record.description:
        return record.description

    for line in record.body.splitlines():
        line_text, line_terms = _field_match_data(line)
        if phrase and _phrase_matches(line_text, line_terms, phrase):
            return _clip(line.strip())

    for term in _query_terms(query):
        if term in description_terms:
            return record.description
        if term in name_terms and record.description:
            return record.description
        for line in record.body.splitlines():
            if term in _field_match_data(line)[1]:
                return _clip(line.strip())

    return _clip(record.body.strip() or record.description or record.name)


def _clip(value: str, limit: int = 280) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in _WORD_PATTERN.findall(query.lower().replace("-", " ").replace("_", " ")):
        if len(term) < 3 and term not in MEANINGFUL_SHORT_TERMS and not any(char.isdigit() for char in term):
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _normalize_search_text(value: str) -> str:
    return " ".join(_WORD_PATTERN.findall(value.lower().replace("-", " ").replace("_", " ")))


def _field_match_data(value: str) -> tuple[str, set[str]]:
    normalized = _normalize_search_text(value)
    return normalized, set(normalized.split())


def _phrase_matches(field_text: str, field_terms: set[str], phrase: str) -> bool:
    if not phrase:
        return False
    if " " not in phrase:
        return phrase in field_terms
    return phrase in field_text


def _paths_signature(paths_with_sources: list[tuple[Path, str]]) -> tuple[tuple[str, str, int | None, int | None], ...]:
    signature: list[tuple[str, str, int | None, int | None]] = []
    for path, source in paths_with_sources:
        try:
            stat_result = path.stat()
            mtime_ns: int | None = stat_result.st_mtime_ns
            size: int | None = stat_result.st_size
        except OSError:
            mtime_ns = None
            size = None
        signature.append((source, str(path), mtime_ns, size))
    return tuple(sorted(signature))


def _parse_sections(body: str) -> list[dict[str, Any]]:
    lines = body.splitlines()
    headings: list[dict[str, Any]] = []
    id_counts: dict[str, int] = defaultdict(int)
    fence_marker: str | None = None

    for index, line in enumerate(lines):
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match:
            marker = fence_match.group("fence")[0]
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            continue
        if fence_marker is not None:
            continue

        heading_match = _ATX_HEADING_PATTERN.match(line)
        if not heading_match:
            continue
        raw_title = heading_match.group("title") or ""
        title = raw_title.strip()
        if title.endswith("#"):
            title = title.rstrip("#").rstrip()
        if not title:
            continue
        base_id = _section_id(title) or "section"
        id_counts[base_id] += 1
        section_id = base_id if id_counts[base_id] == 1 else f"{base_id}-{id_counts[base_id]}"
        headings.append(
            {
                "id": section_id,
                "base_id": base_id,
                "title": title,
                "level": len(heading_match.group("marks")),
                "start_index": index,
                "start_line": index + 1,
            }
        )

    for index, heading in enumerate(headings):
        end_index = len(lines)
        for next_heading in headings[index + 1 :]:
            if next_heading["level"] <= heading["level"]:
                end_index = next_heading["start_index"]
                break
        heading["end_index"] = end_index
        heading["end_line"] = end_index
        heading["content"] = "\n".join(lines[heading["start_index"] : end_index])

    return headings


def _section_summary(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": section["title"],
        "id": section["id"],
        "level": section["level"],
        "start_line": section["start_line"],
        "end_line": section["end_line"],
    }


def _resolve_section(section: str, sections: list[dict[str, Any]]) -> dict[str, Any]:
    query = section.strip()
    normalized_query = _section_id(query)
    if not normalized_query:
        return {"error": "section_not_found", "section": section}

    exact_id_matches = [item for item in sections if item["id"].lower() == query.lower()]
    if len(exact_id_matches) == 1:
        return exact_id_matches[0]
    if len(exact_id_matches) > 1:
        return {
            "error": "ambiguous_section",
            "section": section,
            "matches": [_section_summary(item) for item in exact_id_matches],
        }

    title_matches = [item for item in sections if item["title"].casefold() == query.casefold()]
    if len(title_matches) == 1:
        return title_matches[0]
    if len(title_matches) > 1:
        return {
            "error": "ambiguous_section",
            "section": section,
            "matches": [_section_summary(item) for item in title_matches],
        }

    base_matches = [item for item in sections if item["base_id"] == normalized_query]
    if len(base_matches) == 1:
        return base_matches[0]
    if len(base_matches) > 1:
        return {
            "error": "ambiguous_section",
            "section": section,
            "matches": [_section_summary(item) for item in base_matches],
        }

    return {"error": "section_not_found", "section": section}


def _section_id(title: str) -> str:
    return "-".join(_WORD_PATTERN.findall(title.lower()))


def _root_status(root: Path, *, enabled: bool) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(root),
        "enabled": enabled,
        "exists": None,
        "is_dir": None,
        "readable": None,
    }
    if not enabled:
        return status
    try:
        exists = root.exists()
        is_dir = root.is_dir() if exists else False
        readable = os.access(root, os.R_OK) if exists else False
    except OSError:
        exists = False
        is_dir = False
        readable = False
    status.update({"exists": exists, "is_dir": is_dir, "readable": readable})
    return status


def _records_by_normalized_name(records: list[SkillRecord]) -> dict[str, list[SkillRecord]]:
    groups: dict[str, list[SkillRecord]] = defaultdict(list)
    for record in records:
        groups[record.name.strip().lower()].append(record)
    return groups


def _record_validation_findings(record: SkillRecord) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for error in record.errors:
        if error == "missing name":
            findings.append(
                _finding("missing_name", "error", "Skill frontmatter is missing a non-empty name.", record)
            )
        elif error == "missing description":
            findings.append(
                _finding(
                    "missing_description",
                    "error",
                    "Skill frontmatter is missing a non-empty description.",
                    record,
                )
            )
        elif error == "empty description":
            findings.append(
                _finding("missing_description", "error", "Skill frontmatter description is empty.", record)
            )
        elif error.startswith("malformed frontmatter"):
            findings.append(_finding("malformed_frontmatter", "error", error, record))
        elif error.startswith("read error"):
            findings.append(_finding("read_error", "error", error, record))
        else:
            findings.append(_finding("validation_error", "error", error, record))

    if record.description and len(record.description) > LONG_DESCRIPTION_CHARS:
        findings.append(
            _finding(
                "long_description",
                "warning",
                f"Description is {len(record.description)} characters; concise descriptions improve skill discovery.",
                record,
            )
        )
    return findings


def _duplicate_name_findings(records: list[SkillRecord]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for normalized_name, group in _records_by_normalized_name(records).items():
        if len(group) < 2:
            continue
        sources = {record.source for record in group}
        if len(sources) == 1:
            severity = "error"
            duplicate_scope = "same_source"
            message = f"Duplicate skill name '{group[0].name}' appears {len(group)} times in source '{group[0].source}'."
        else:
            severity = "warning"
            duplicate_scope = "cross_source"
            message = f"Duplicate skill name '{group[0].name}' appears across sources: {', '.join(sorted(sources))}."
        findings.append(
            _finding(
                "duplicate_name",
                severity,
                message,
                affected_records=[_compact_record(record) for record in sorted(group, key=_record_sort_key)],
                name=normalized_name,
                duplicate_scope=duplicate_scope,
            )
        )
    return findings


def _finding(
    code: str,
    severity: str,
    message: str,
    record: SkillRecord | None = None,
    *,
    affected_records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if affected_records is None:
        affected_records = [_compact_record(record)] if record is not None else []
    finding = {
        "code": code,
        "severity": severity,
        "message": message,
        "affected_records": affected_records,
    }
    finding.update(extra)
    return finding


def _compact_record(record: SkillRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "folder": record.folder,
        "source": record.source,
        "path": str(record.path),
        "valid": record.valid,
        "errors": list(record.errors),
    }

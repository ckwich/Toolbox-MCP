from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_SOURCES = {"user", "system", "runtime", "plugin"}
DEFAULT_MAX_CHARS = 20_000
MAX_CONTENT_CHARS = 100_000


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
            "errors": self.errors,
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
        content = record.content[:safe_max]
        return {
            "error": None,
            "skill": record.summary(),
            "frontmatter": record.frontmatter,
            "content": content,
            "content_chars": len(record.content),
            "truncated": len(record.content) > len(content),
        }

    def search_skills(
        self,
        query: str,
        *,
        limit: int = 10,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, Any]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            return {"error": "empty_query", "count": 0, "results": []}

        records = self._discover_records(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )
        scored = [
            (score, record)
            for record in records
            if (score := _score_record(record, normalized_query)) > 0
        ]
        scored.sort(key=lambda item: (-item[0], item[1].name, str(item[1].path)))
        safe_limit = max(1, min(limit, 100))
        results = []
        for score, record in scored[:safe_limit]:
            summary = record.summary()
            summary["score"] = score
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
                include_system=include_system,
                include_runtime=include_runtime,
                include_plugins=include_plugins,
            )

        validations = [
            {
                **record.summary(),
                "frontmatter_keys": sorted(record.frontmatter),
            }
            for record in records
        ]
        return {
            "error": None,
            "count": len(validations),
            "valid": all(item["valid"] for item in validations),
            "validations": validations,
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
        records: list[SkillRecord] = []
        seen: set[Path] = set()

        for path in self._iter_skill_paths(self.skills_root):
            source = self._source_for(path)
            if source == "system" and not include_system:
                continue
            if source == "runtime" and not include_runtime:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            records.append(self._read_record(path, source))

        if include_plugins:
            for path in self._iter_skill_paths(self.plugin_cache):
                if not _is_relative_to(path, self.plugin_cache):
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                records.append(self._read_record(path, "plugin"))

        return sorted(records, key=_record_sort_key)

    def _iter_skill_paths(self, root: Path) -> list[Path]:
        if not root.exists() or not root.is_dir():
            return []
        return sorted(path for path in root.rglob("SKILL.md") if path.is_file())

    def _source_for(self, path: Path) -> str:
        if _is_relative_to(path, self.plugin_cache):
            return "plugin"
        system_root = self.skills_root / ".system"
        runtime_root = self.skills_root / "codex-primary-runtime"
        if _is_relative_to(path, system_root):
            return "system"
        if _is_relative_to(path, runtime_root):
            return "runtime"
        return "user"

    def _read_record(self, path: Path, source: str) -> SkillRecord:
        errors: list[str] = []
        content = ""
        frontmatter: dict[str, str] = {}
        body = ""
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, body = _parse_frontmatter(content)
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"read error: {exc}")

        folder = path.parent.name
        name = frontmatter.get("name", folder).strip()
        description = frontmatter.get("description", "").strip()
        if "name" not in frontmatter or not name:
            errors.append("missing name")
            name = folder
        if "description" not in frontmatter or not description:
            errors.append("missing description")

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


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}, content

    frontmatter: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        frontmatter[key] = value.strip().strip("\"'")

    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return frontmatter, body


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


def _score_record(record: SkillRecord, query: str) -> int:
    name = record.name.lower()
    description = record.description.lower()
    body = record.body.lower()
    score = 0
    if name == query:
        score += 20
    if query in name:
        score += 10
    if query in description:
        score += 6
    if query in body:
        score += 2
    return score


def _build_snippet(record: SkillRecord, query: str) -> str:
    if query in record.description.lower():
        return record.description
    if query in record.name.lower() and record.description:
        return record.description

    for line in record.body.splitlines():
        if query in line.lower():
            return _clip(line.strip())

    return _clip(record.body.strip() or record.description or record.name)


def _clip(value: str, limit: int = 280) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

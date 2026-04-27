from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import types
from pydantic import ValidationError

from toolbox.config import configured_state_path, default_fake_manifest_path
from toolbox.models import (
    ActivationResult,
    AuditQuery,
    AuditQueryResult,
    AuditOperation,
    AuditOutcome,
    BatchRunResult,
    BatchStep,
    BatchStepResult,
    ContractAvailability,
    ContractDiffResult,
    ContractInspectionResult,
    ClearStaleResult,
    ErrorInfo,
    HealthCheckResult,
    HealthStatus,
    MountedTool,
    MountedToolDescription,
    MountedToolDescriptionResult,
    ProgramRuntimeBudget,
    ProgramRunResult,
    RestoreResult,
    RuntimeBudgetInspection,
    SchemaSnapshot,
    SchemaShapeSummary,
    Scope,
    StoredAuditEvent,
    ToolSchema,
    ToolContractDelta,
    ToolContractSummary,
    ToolboxBrief,
    ToolboxCatalogAudit,
    ToolboxCatalogIssue,
    ToolboxCategoryOverview,
    ToolboxOverview,
    ToolsetActivationPlan,
    ToolsetActivationPlanStep,
    ToolsetContractDiff,
    ToolsetContractInspection,
    ToolsetGuide,
    ToolsetRecord,
    ToolsetSuggestion,
    ToolsetSuggestionResult,
    ToolsetSummary,
    ToolsetTransport,
    TransportRuntimeBudget,
    TransportState,
)
from toolbox.program_runtime import ToolProgramRuntime
from toolbox.registry import JsonStateStore
from toolbox.scope_manager import ScopeManager
from toolbox.transport_manager import TransportManager
from toolbox.validation import diff_toolsets, inventory_hash, normalize_tool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolboxService:
    """Core lifecycle logic behind the Toolbox MCP control-plane tools."""

    def __init__(
        self,
        state_path: Path | None = None,
        *,
        restore_on_startup: bool = False,
        stable_host_identity: bool = False,
    ) -> None:
        self.state_path = state_path or configured_state_path()
        self.restore_on_startup = restore_on_startup
        self.stable_host_identity = stable_host_identity
        self.store = JsonStateStore(self.state_path)
        self.transport_manager = TransportManager()
        self.scope_manager = ScopeManager()
        self._namespace_locks: dict[str, asyncio.Lock] = {}
        self.workspace = self.state_path.parent.parent
        self.fake_manifest_path = default_fake_manifest_path(self.workspace)
        self._bootstrap_defaults()
        self._reconcile_scope_state_on_startup()

    def _bootstrap_defaults(self) -> None:
        self.store.ensure()
        self.fake_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.fake_manifest_path.exists():
            manifest = {
                "tools": [
                    {
                        "name": "fake_status",
                        "title": "Fake Status",
                        "description": "Return a canned status payload from the fake managed toolset.",
                        "response": {"status": "ok", "source": "fake-toolset-v1"},
                    }
                ]
            }
            self.fake_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        namespace = "fake_stdio"
        if self.store.get_toolset(namespace) is not None:
            return

        command = sys.executable
        cwd = str(self.workspace)
        record = ToolsetRecord(
            namespace=namespace,
            title="Fake Managed Stdio Toolset",
            description="A fake managed MCP server used to prove activation, refresh, and deactivation flows.",
            tags=["fake", "test", "stdio", "managed"],
            category="testing",
            aliases=["fake toolset", "managed test server"],
            examples=[
                "Prove a host can activate and call a managed toolset.",
                "Smoke-test Toolbox registration and lifecycle behavior.",
            ],
            recipes=[
                "Use toolbox_brief for orientation, activate fake_stdio, call fake_stdio.fake_status, then deactivate when the smoke check is complete.",
            ],
            activation_hint="Activate this when validating Toolbox itself or exercising the fake stdio flow.",
            cost_hint="low",
            latency_hint="low",
            trust_hint="local-test",
            transport={
                "kind": "stdio",
                "command": command,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(self.fake_manifest_path)],
                "env": {"PYTHONPATH": _merged_pythonpath(cwd)},
                "cwd": cwd,
            },
            default_scope=Scope.THREAD,
        )
        self.store.upsert_toolset(record)

    def _load_all_records(self) -> list[ToolsetRecord]:
        return self.store.list_toolsets()

    def _get_record(self, namespace: str) -> ToolsetRecord | None:
        return self.store.get_toolset(namespace)

    def _namespace_lock(self, namespace: str) -> asyncio.Lock:
        lock = self._namespace_locks.get(namespace)
        if lock is None:
            lock = asyncio.Lock()
            self._namespace_locks[namespace] = lock
        return lock

    def _reconcile_scope_state_on_startup(self) -> None:
        for record in self._load_all_records():
            changed = False
            recoverable_scopes = list(record.recoverable_scopes)
            if not recoverable_scopes:
                recoverable_scopes = [
                    scope for scope in record.loaded_scopes if scope in record.restorable_scopes
                ]
            if record.recoverable_scopes != recoverable_scopes:
                record.recoverable_scopes = recoverable_scopes
                changed = True

            if record.loaded_scopes:
                record.loaded_scopes = []
                changed = True

            if record.transport_state is not TransportState.INACTIVE:
                record.transport_state = TransportState.INACTIVE
                changed = True

            if changed:
                self.store.upsert_toolset(record)

    @staticmethod
    def _is_valid_namespace(namespace: str) -> bool:
        if not namespace:
            return False
        return all(character.isalnum() or character in {"_", "-"} for character in namespace)

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        namespace: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ToolboxService._error_info(
            code,
            message,
            namespace=namespace,
            retryable=retryable,
            details=details,
        ).model_dump(mode="json")

    @staticmethod
    def _error_info(
        code: str,
        message: str,
        *,
        namespace: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ErrorInfo:
        return ErrorInfo(
            code=code,
            message=message,
            namespace=namespace,
            retryable=retryable,
            details=details,
        )

    @staticmethod
    def _audit_scope(record: ToolsetRecord | None, preferred: Scope | None = None) -> Scope | None:
        if preferred is not None:
            return preferred
        if record is not None and len(record.loaded_scopes) == 1:
            return record.loaded_scopes[0]
        return None

    def _scope_policy(self, record: ToolsetRecord) -> dict[str, Any]:
        return self.scope_manager.scope_policy(record)

    def _append_audit_event(
        self,
        *,
        operation: AuditOperation,
        outcome: AuditOutcome,
        namespace: str | None = None,
        scope: Scope | None = None,
        details: dict[str, Any] | None = None,
        error: ErrorInfo | None = None,
    ) -> None:
        self.store.append_audit_event(
            StoredAuditEvent(
                timestamp=utc_now(),
                operation=operation,
                outcome=outcome,
                namespace=namespace,
                scope=scope,
                details=details or {},
                error=error,
            )
        )

    def search_toolsets(self, query: str, limit: int = 10, include_inactive: bool = True) -> dict[str, Any]:
        query_terms = self._discovery_terms(query)
        ranked: list[tuple[int, ToolsetRecord, list[str]]] = []
        for record in self._load_all_records():
            if not include_inactive and not record.loaded_scopes:
                continue
            score, reasons = self._score_record_for_terms(record, query_terms)
            if score > 0 or not query_terms:
                ranked.append((score, record, reasons))

        ranked.sort(key=lambda item: (-item[0], item[1].namespace))
        results = [
            {
                "namespace": record.namespace,
                "title": record.title,
                "description": record.description,
                "category": record.category,
                "tags": record.tags,
                "aliases": record.aliases,
                "examples": record.examples[:3],
                "recipes": record.recipes[:3],
                "activation_hint": record.activation_hint,
                "cost_hint": record.cost_hint,
                "latency_hint": record.latency_hint,
                "trust_hint": record.trust_hint,
                "score": score,
                "reasons": reasons,
                "transport": record.transport.kind,
                "loaded": bool(record.loaded_scopes),
                "stale": record.stale,
            }
            for score, record, reasons in ranked[:limit]
        ]
        return {"query": query, "count": len(results), "results": results, "error": None}

    def toolbox_overview(self, max_toolsets_per_category: int = 5) -> dict[str, Any]:
        records = sorted(self._load_all_records(), key=lambda item: (item.category, item.namespace))
        categories: dict[str, list[ToolsetRecord]] = {}
        for record in records:
            categories.setdefault(record.category, []).append(record)

        category_overviews: list[ToolboxCategoryOverview] = []
        for category, category_records in sorted(categories.items()):
            examples = self._dedupe_strings(
                example
                for record in category_records
                for example in record.examples[:2]
            )[:3]
            category_overviews.append(
                ToolboxCategoryOverview(
                    category=category,
                    count=len(category_records),
                    loaded_count=sum(1 for record in category_records if record.loaded_scopes),
                    stale_count=sum(1 for record in category_records if record.stale),
                    examples=examples,
                    toolsets=[
                        self._toolset_summary(record)
                        for record in category_records[: max(0, max_toolsets_per_category)]
                    ],
                )
            )

        overview = ToolboxOverview(
            purpose=(
                "Toolbox keeps deferred MCP toolsets discoverable without loading every downstream "
                "tool schema into the always-on surface."
            ),
            when_to_use=[
                "Search Toolbox when the visible tools do not cover the task.",
                "Search Toolbox when a task mentions skills, docs, scans, SaaS systems, browser/app automation, or project-specific capabilities.",
                "Activate only the selected toolsets needed for the current task, then deactivate them when done.",
            ],
            discovery_tools=[
                {
                    "name": "toolbox_brief",
                    "use": "Get the lowest-token orientation, active toolset snapshot, and next action hints for the current task.",
                },
                {
                    "name": "search_toolsets",
                    "use": "Find registered toolsets by keyword, category, alias, example, or activation hint.",
                },
                {
                    "name": "suggest_toolsets_for_task",
                    "use": "Ask Toolbox for a short ranked list of toolsets relevant to the current task.",
                },
                {
                    "name": "plan_toolset_activation",
                    "use": "Build a dry-run activation plan before mounting deferred toolsets.",
                },
                {
                    "name": "get_toolset_guide",
                    "use": "Load compact recipes and operational hints for one selected toolset.",
                },
                {
                    "name": "audit_toolbox_catalog",
                    "use": "Find thin or under-described registrations that agents may struggle to use.",
                },
                {
                    "name": "activate_toolsets",
                    "use": "Mount selected downstream tools under namespace.tool names after choosing a toolset.",
                },
            ],
            category_count=len(category_overviews),
            toolset_count=len(records),
            categories=category_overviews,
        )
        payload = overview.model_dump(mode="json")
        payload["error"] = None
        return payload

    def suggest_toolsets_for_task(
        self,
        task: str,
        limit: int = 5,
        include_inactive: bool = True,
    ) -> dict[str, Any]:
        task_terms = self._discovery_terms(task)
        if not task_terms:
            return {
                "task": task,
                "count": 0,
                "suggestions": [],
                "searched_fields": self._agent_discovery_fields(),
                "error": self._error("invalid_task", "Task must include at least one search term."),
            }

        ranked: list[tuple[int, ToolsetRecord, list[str]]] = []
        for record in self._load_all_records():
            if not include_inactive and not record.loaded_scopes:
                continue
            score, reasons = self._score_record_for_terms(record, task_terms)
            if score > 0:
                ranked.append((score, record, reasons))

        ranked.sort(key=lambda item: (-item[0], item[1].stale, item[1].namespace))
        suggestions = [
            ToolsetSuggestion(
                namespace=record.namespace,
                title=record.title,
                description=record.description,
                category=record.category,
                score=score,
                reasons=reasons[:5],
                tags=record.tags,
                aliases=record.aliases,
                examples=record.examples[:3],
                recipes=record.recipes[:3],
                activation_hint=record.activation_hint,
                cost_hint=record.cost_hint,
                latency_hint=record.latency_hint,
                trust_hint=record.trust_hint,
                loaded=bool(record.loaded_scopes),
                stale=record.stale,
                transport_state=record.transport_state,
                tool_count=record.tool_count,
                auth_required=record.auth_required,
            )
            for score, record, reasons in ranked[: max(0, limit)]
        ]
        result = ToolsetSuggestionResult(
            task=task,
            count=len(suggestions),
            suggestions=suggestions,
            searched_fields=self._agent_discovery_fields(),
        )
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    def toolbox_brief(
        self,
        task: str | None = None,
        max_suggestions: int = 3,
        max_active: int = 5,
    ) -> dict[str, Any]:
        normalized_task = self._clean_optional_text(task)
        records = sorted(self._load_all_records(), key=lambda item: item.namespace)
        active_toolsets = [
            self._toolset_summary(record)
            for record in records
            if record.loaded_scopes
        ][: max(0, max_active)]

        suggestions: list[ToolsetSuggestion] = []
        warnings: list[str] = []
        if normalized_task is not None:
            suggestion_payload = self.suggest_toolsets_for_task(
                normalized_task,
                limit=max(0, max_suggestions),
                include_inactive=True,
            )
            if suggestion_payload["error"] is None:
                suggestions = [
                    ToolsetSuggestion.model_validate(item)
                    for item in suggestion_payload["suggestions"]
                ]
            else:
                warnings.append(suggestion_payload["error"]["message"])

        stale_count = sum(1 for record in records if record.stale)
        if stale_count:
            warnings.append(f"{stale_count} registered toolset(s) are currently stale.")

        next_actions: list[dict[str, Any]] = [
            {
                "tool": "toolbox_overview",
                "when": "Use when you need the catalog shape by category.",
            },
            {
                "tool": "search_toolsets",
                "when": "Use when you know keywords but not the right namespace.",
            },
        ]
        if normalized_task is not None:
            next_actions.insert(
                0,
                {
                    "tool": "plan_toolset_activation",
                    "when": "Use before activation to keep mounting deliberate and scoped.",
                    "task": normalized_task,
                },
            )
        else:
            next_actions.insert(
                0,
                {
                    "tool": "suggest_toolsets_for_task",
                    "when": "Use when you have a task description and want ranked candidates.",
                },
            )

        brief = ToolboxBrief(
            task=normalized_task,
            purpose=(
                "Use Toolbox as a progressive-discovery control plane: orient, search or suggest, "
                "plan activation, mount only what the task needs, then compose and clean up."
            ),
            recommended_flow=[
                {
                    "tool": "toolbox_brief",
                    "use": "Start here for low-token orientation and task-specific next actions.",
                },
                {
                    "tool": "suggest_toolsets_for_task",
                    "use": "Rank deferred toolsets against the current task.",
                },
                {
                    "tool": "plan_toolset_activation",
                    "use": "Dry-run which namespaces to activate and why.",
                },
                {
                    "tool": "activate_toolsets",
                    "use": "Mount only the selected namespaces for the narrowest useful scope.",
                },
                {
                    "tool": "describe_mounted_tools",
                    "use": "Inspect compact live contracts before composing calls.",
                },
                {
                    "tool": "run_tool_batch or run_tool_program",
                    "use": "Compose downstream calls in one round trip when multiple calls are needed.",
                },
                {
                    "tool": "deactivate_toolsets",
                    "use": "Unmount task-specific toolsets when they are no longer needed.",
                },
            ],
            next_actions=next_actions,
            active_toolsets=active_toolsets,
            suggestions=suggestions,
            warnings=warnings,
        )
        payload = brief.model_dump(mode="json")
        payload["error"] = None
        return payload

    def plan_toolset_activation(
        self,
        task: str,
        limit: int = 3,
        scope: str = "thread",
        include_inactive: bool = True,
    ) -> dict[str, Any]:
        normalized_task = self._clean_optional_text(task)
        if normalized_task is None:
            return {
                "task": task,
                "scope": scope,
                "selected_namespaces": [],
                "already_loaded": [],
                "skipped": [],
                "steps": [],
                "suggestions": [],
                "warnings": [],
                "error": self._error("invalid_task", "Task must include at least one search term."),
            }

        try:
            scope_value = Scope(scope)
        except ValueError:
            return {
                "task": normalized_task,
                "scope": scope,
                "selected_namespaces": [],
                "already_loaded": [],
                "skipped": [],
                "steps": [],
                "suggestions": [],
                "warnings": [],
                "error": self._error("invalid_scope", f"Unsupported scope: {scope}", retryable=False),
            }

        suggestion_payload = self.suggest_toolsets_for_task(
            normalized_task,
            limit=max(0, limit),
            include_inactive=include_inactive,
        )
        if suggestion_payload["error"] is not None:
            return {
                "task": normalized_task,
                "scope": scope_value.value,
                "selected_namespaces": [],
                "already_loaded": [],
                "skipped": [],
                "steps": [],
                "suggestions": [],
                "warnings": [suggestion_payload["error"]["message"]],
                "error": suggestion_payload["error"],
            }

        suggestions = [
            ToolsetSuggestion.model_validate(item)
            for item in suggestion_payload["suggestions"]
        ]
        selected_namespaces: list[str] = []
        already_loaded: list[str] = []
        skipped: list[dict[str, str]] = []
        warnings: list[str] = []

        for suggestion in suggestions:
            record = self._get_record(suggestion.namespace)
            if record is None:
                skipped.append({"namespace": suggestion.namespace, "reason": "registration_missing"})
                continue
            if record.stale:
                skipped.append({"namespace": record.namespace, "reason": "stale"})
                warnings.append(f"{record.namespace} is stale; refresh or clear stale state before activation.")
                continue
            if scope_value in record.loaded_scopes:
                already_loaded.append(record.namespace)
                continue
            selected_namespaces.append(record.namespace)

        steps: list[ToolsetActivationPlanStep] = []
        if selected_namespaces:
            steps.append(
                ToolsetActivationPlanStep(
                    action="activate_toolsets",
                    namespaces=selected_namespaces,
                    scope=scope_value,
                    reason="Mount the selected deferred toolsets for this task only.",
                    tool="activate_toolsets",
                )
            )
            steps.append(
                ToolsetActivationPlanStep(
                    action="inspect_mounted_contracts",
                    namespaces=selected_namespaces,
                    reason="Read compact mounted contracts before calling or composing downstream tools.",
                    tool="inspect_mounted_contracts",
                )
            )
        if already_loaded:
            steps.append(
                ToolsetActivationPlanStep(
                    action="describe_mounted_tools",
                    namespaces=already_loaded,
                    reason="Reuse already-mounted toolsets instead of activating them again.",
                    tool="describe_mounted_tools",
                )
            )
        if not suggestions:
            warnings.append("No registered toolsets matched the task.")

        plan = ToolsetActivationPlan(
            task=normalized_task,
            scope=scope_value,
            selected_namespaces=selected_namespaces,
            already_loaded=already_loaded,
            skipped=skipped,
            steps=steps,
            suggestions=suggestions,
            warnings=warnings,
        )
        payload = plan.model_dump(mode="json")
        payload["error"] = None
        return payload

    def get_toolset_guide(self, namespace: str) -> dict[str, Any]:
        record = self._get_record(namespace)
        if record is None:
            return {
                "toolset": None,
                "when_to_use": [],
                "recipes": [],
                "examples": [],
                "next_actions": [],
                "warnings": [],
                "error": self._error(
                    "unknown_toolset",
                    f"Unknown toolset: {namespace}",
                    namespace=namespace,
                    retryable=False,
                ),
            }

        warnings: list[str] = []
        if record.stale:
            warnings.append("This toolset is stale; refresh it before relying on mounted contracts.")

        next_actions: list[dict[str, Any]]
        if record.loaded_scopes:
            next_actions = [
                {
                    "tool": "describe_mounted_tools",
                    "namespaces": [record.namespace],
                    "when": "Use now because this toolset is already mounted.",
                }
            ]
        else:
            next_actions = [
                {
                    "tool": "activate_toolsets",
                    "namespaces": [record.namespace],
                    "scope": record.default_scope.value,
                    "when": "Use when this guide confirms the toolset is relevant to the current task.",
                }
            ]
        next_actions.extend(
            [
                {
                    "tool": "inspect_cached_contracts",
                    "namespaces": [record.namespace],
                    "when": "Use for compact cached schema summaries before activation.",
                },
                {
                    "tool": "plan_toolset_activation",
                    "when": "Use with a task string when choosing among multiple candidate toolsets.",
                },
            ]
        )

        guide = ToolsetGuide(
            toolset=self._toolset_summary(record),
            when_to_use=self._dedupe_strings(
                [
                    record.activation_hint,
                    *record.examples,
                    *record.recipes,
                ]
            ),
            recipes=record.recipes,
            examples=record.examples,
            next_actions=next_actions,
            warnings=warnings,
        )
        payload = guide.model_dump(mode="json")
        payload["error"] = None
        return payload

    def audit_toolbox_catalog(self) -> dict[str, Any]:
        checked_fields = [
            "category",
            "aliases",
            "examples_or_recipes",
            "activation_hint",
            "cost_hint",
            "latency_hint",
            "trust_hint",
        ]
        issues: list[ToolboxCatalogIssue] = []
        for record in sorted(self._load_all_records(), key=lambda item: item.namespace):
            missing_fields: list[str] = []
            suggestions: list[str] = []
            if record.category == "general":
                missing_fields.append("category")
                suggestions.append("Set a domain category so agents can browse related toolsets.")
            if not record.aliases:
                missing_fields.append("aliases")
                suggestions.append("Add aliases that match how agents or humans describe the capability.")
            if not record.examples and not record.recipes:
                missing_fields.append("examples_or_recipes")
                suggestions.append("Add examples or recipes so agents can recognize when to use the toolset.")
            if record.activation_hint is None:
                missing_fields.append("activation_hint")
                suggestions.append("Explain the trigger condition for mounting this toolset.")
            if record.cost_hint is None:
                missing_fields.append("cost_hint")
                suggestions.append("Add a compact cost hint such as low, medium, high, or external-paid.")
            if record.latency_hint is None:
                missing_fields.append("latency_hint")
                suggestions.append("Add a compact latency hint such as low, medium, or high.")
            if record.trust_hint == "unknown":
                missing_fields.append("trust_hint")
                suggestions.append("Describe the trust boundary, for example local, authenticated SaaS, or external.")

            if missing_fields:
                issues.append(
                    ToolboxCatalogIssue(
                        namespace=record.namespace,
                        severity="warning",
                        missing_fields=missing_fields,
                        suggestions=suggestions,
                    )
                )

        audit = ToolboxCatalogAudit(
            toolset_count=len(self._load_all_records()),
            issue_count=len(issues),
            issues=issues,
            checked_fields=checked_fields,
        )
        payload = audit.model_dump(mode="json")
        payload["error"] = None
        return payload

    def list_toolsets(self, scope: str | None = None) -> dict[str, Any]:
        scope_value = None
        if scope is not None:
            try:
                scope_value = Scope(scope)
            except ValueError:
                return {
                    "count": 0,
                    "toolsets": [],
                    "error": self._error("invalid_scope", f"Unsupported scope: {scope}", retryable=False),
                }

        toolsets = []
        for record in self._load_all_records():
            if scope_value is not None and scope_value not in record.loaded_scopes:
                continue
            toolsets.append(
                {
                    "namespace": record.namespace,
                    "title": record.title,
                    "description": record.description,
                    "category": record.category,
                    "tags": record.tags,
                    "aliases": record.aliases,
                    "examples": record.examples[:3],
                    "recipes": record.recipes[:3],
                    "activation_hint": record.activation_hint,
                    "cost_hint": record.cost_hint,
                    "latency_hint": record.latency_hint,
                    "trust_hint": record.trust_hint,
                    "loaded": bool(record.loaded_scopes),
                    "scope": [item.value for item in record.loaded_scopes],
                    "recoverable_scopes": [item.value for item in record.recoverable_scopes],
                    "scope_policy": self._scope_policy(record),
                    "health_status": record.last_health_status.value if record.last_health_status is not None else None,
                    "last_health_checked_at": _iso(record.last_health_checked_at),
                    "last_restored_at": _iso(record.last_restored_at),
                    "stale": record.stale,
                    "transport_state": record.transport_state.value,
                    "schema_hash": record.schema_hash,
                    "tool_count": record.tool_count,
                    "mounted_tools": [tool.mounted_name for tool in self.list_mounted_tools(record.namespace)],
                    "last_refresh_at": _iso(record.last_refreshed_at),
                    "last_error": record.last_error.model_dump(mode="json") if record.last_error else None,
                }
            )

        return {"count": len(toolsets), "toolsets": toolsets, "error": None}

    def inspect_cached_contracts(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        selected = namespaces or [record.namespace for record in self._load_all_records()]
        result = ContractInspectionResult(
            count=0,
            contracts=[self._build_contract_inspection(namespace, source="cached") for namespace in selected],
        )
        result.count = len(result.contracts)
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    def inspect_mounted_contracts(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        selected = namespaces or self.transport_manager.mounted_namespaces()
        result = ContractInspectionResult(
            count=0,
            contracts=[self._build_contract_inspection(namespace, source="mounted") for namespace in selected],
        )
        result.count = len(result.contracts)
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    def describe_mounted_tools(
        self,
        namespaces: list[str] | None = None,
        mounted_names: list[str] | None = None,
    ) -> dict[str, Any]:
        active_namespaces = self.transport_manager.mounted_namespaces()
        descriptions = [self._build_mounted_tool_description(tool) for tool in self.list_mounted_tools()]
        if namespaces is not None:
            namespace_filter = set(namespaces)
            descriptions = [item for item in descriptions if item.namespace in namespace_filter]

        missing: list[dict[str, Any]] = []
        if mounted_names is not None:
            descriptions_by_name = {item.mounted_name: item for item in descriptions}
            selected_descriptions: list[MountedToolDescription] = []
            for mounted_name in mounted_names:
                description = descriptions_by_name.get(mounted_name)
                if description is not None:
                    selected_descriptions.append(description)
                    continue
                missing.append(
                    {
                        "mounted_name": mounted_name,
                        "reason": self._mounted_tool_missing_reason(mounted_name, active_namespaces),
                    }
                )
            descriptions = selected_descriptions
        else:
            descriptions.sort(key=lambda item: item.mounted_name)

        if namespaces is not None:
            for namespace in namespaces:
                if namespace in active_namespaces:
                    continue
                missing.append(
                    {
                        "namespace": namespace,
                        "reason": "unknown_toolset" if self._get_record(namespace) is None else "not_mounted",
                    }
                )

        result = MountedToolDescriptionResult(
            count=len(descriptions),
            descriptions=descriptions,
            missing=missing,
        )
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    def inspect_runtime_budgets(self) -> dict[str, Any]:
        inspection = RuntimeBudgetInspection(
            program=ProgramRuntimeBudget(**ToolProgramRuntime.budget_settings()),
            transport=TransportRuntimeBudget(**self.transport_manager.budget_settings()),
        )
        payload = inspection.model_dump(mode="json")
        payload["error"] = None
        return payload

    def diff_cached_contracts(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        selected = namespaces or [record.namespace for record in self._load_all_records()]
        return self._diff_contracts(selected, source="cached")

    def diff_mounted_contracts(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        selected = namespaces or self.transport_manager.mounted_namespaces()
        return self._diff_contracts(selected, source="mounted")

    def list_audit_events(
        self,
        *,
        namespace: str | None = None,
        operation: str | None = None,
        outcome: str | None = None,
        registration_present: bool | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        try:
            query = AuditQuery.model_validate(
                {
                    "namespace": namespace,
                    "operation": operation,
                    "outcome": outcome,
                    "registration_present": registration_present,
                    "limit": limit,
                }
            )
        except ValidationError as exc:
            return {
                "count": 0,
                "events": [],
                "error": self._error(
                    "invalid_audit_query",
                    "Audit query filters are invalid.",
                    details={"validation_error": str(exc)},
                ),
            }

        if query.limit < 0:
            return {
                "count": 0,
                "events": [],
                "error": self._error(
                    "invalid_audit_query",
                    "Audit query limit must be zero or greater.",
                    details={"limit": query.limit},
                ),
            }

        bounded_limit = min(query.limit, self.store.audit_retention_limit)
        result = AuditQueryResult(
            count=0,
            events=self.store.list_audit_events(
                namespace=query.namespace,
                operation=query.operation,
                outcome=query.outcome,
                registration_present=query.registration_present,
                limit=bounded_limit,
            ),
        )
        result.count = len(result.events)
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    async def check_toolset_health(
        self,
        namespaces: list[str] | None = None,
        *,
        scope: str | None = None,
    ) -> dict[str, Any]:
        scope_value = None
        if scope is not None:
            try:
                scope_value = Scope(scope)
            except ValueError:
                error = self._error_info("invalid_scope", f"Unsupported scope: {scope}")
                self._append_audit_event(
                    operation=AuditOperation.HEALTH_CHECK,
                    outcome=AuditOutcome.FAILURE,
                    error=error,
                )
                return {"checked": [], "skipped": [], "failed": [], "error": error.model_dump(mode="json")}

        candidates = namespaces or [record.namespace for record in self._load_all_records() if record.loaded_scopes]
        checked: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in candidates:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.HEALTH_CHECK,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue
                if not record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "not_active"})
                    continue
                if scope_value is not None and scope_value not in record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "scope_not_active", "scope": scope_value.value})
                    continue

                runtime = self.transport_manager.runtime_for(namespace)
                if runtime is None:
                    error = self._record_runtime_failure(
                        namespace,
                        "runtime_not_connected",
                        f"Toolset {namespace} has active scopes but no connected runtime.",
                        stale_reason="healthcheck_runtime_missing",
                    )
                    health_result = self._record_health_observation(
                        namespace,
                        status=HealthStatus.FAILED,
                        observed_schema_hash=None,
                        observed_tool_count=None,
                        error=error,
                    )
                    self._append_audit_event(
                        operation=AuditOperation.HEALTH_CHECK,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=self._audit_scope(record, scope_value),
                        details={"stale_reason": "healthcheck_runtime_missing"},
                        error=error,
                    )
                    checked.append(health_result.model_dump(mode="json"))
                    continue

                try:
                    probe = await self.transport_manager.probe_runtime(namespace)
                except Exception as exc:
                    await self.transport_manager.disconnect(namespace)
                    error_code = "healthcheck_timeout" if isinstance(exc, TimeoutError) else "healthcheck_failed"
                    stale_reason = "healthcheck_timeout" if error_code == "healthcheck_timeout" else "healthcheck_failed"
                    error = self._record_runtime_failure(
                        namespace,
                        error_code,
                        str(exc),
                        stale_reason=stale_reason,
                    )
                    health_result = self._record_health_observation(
                        namespace,
                        status=HealthStatus.FAILED,
                        observed_schema_hash=None,
                        observed_tool_count=None,
                        error=error,
                    )
                    self._append_audit_event(
                        operation=AuditOperation.HEALTH_CHECK,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=self._audit_scope(record, scope_value),
                        details={"stale_reason": stale_reason},
                        error=error,
                    )
                    checked.append(health_result.model_dump(mode="json"))
                    continue

                observed_hash = inventory_hash(probe.normalized_tools)
                observed_tool_count = len(probe.normalized_tools)
                drift_detected = record.schema_hash is not None and observed_hash != record.schema_hash

                if drift_detected:
                    drift_error = self._record_runtime_failure(
                        namespace,
                        "healthcheck_schema_changed",
                        f"Toolset {namespace} reported a schema hash that differs from the cached contract.",
                        stale_reason="healthcheck_schema_changed",
                        details={
                            "cached_schema_hash": record.schema_hash,
                            "observed_schema_hash": observed_hash,
                        },
                    )
                    health_result = self._record_health_observation(
                        namespace,
                        status=HealthStatus.STALE,
                        observed_schema_hash=observed_hash,
                        observed_tool_count=observed_tool_count,
                        error=drift_error,
                    )
                    self._append_audit_event(
                        operation=AuditOperation.HEALTH_CHECK,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=self._audit_scope(record, scope_value),
                        details={
                            "stale_reason": "healthcheck_schema_changed",
                            "cached_schema_hash": record.schema_hash,
                            "observed_schema_hash": observed_hash,
                        },
                        error=drift_error,
                    )
                    checked.append(health_result.model_dump(mode="json"))
                    continue

                health_result = self._record_health_observation(
                    namespace,
                    status=HealthStatus.HEALTHY,
                    observed_schema_hash=observed_hash,
                    observed_tool_count=observed_tool_count,
                    error=None,
                )
                self._append_audit_event(
                    operation=AuditOperation.HEALTH_CHECK,
                    outcome=AuditOutcome.SUCCESS,
                    namespace=namespace,
                    scope=self._audit_scope(record, scope_value),
                    details={
                        "observed_schema_hash": observed_hash,
                        "observed_tool_count": observed_tool_count,
                    },
                )
                checked.append(health_result.model_dump(mode="json"))

        return {"checked": checked, "skipped": skipped, "failed": failed, "error": None}

    async def maybe_restore_on_startup(self) -> dict[str, Any]:
        if not self.restore_on_startup:
            return {"restored": [], "skipped": [], "failed": [], "error": None}
        return await self.restore_toolsets(
            namespaces=None,
            stable_host_identity=self.stable_host_identity,
            startup=True,
        )

    async def restore_toolsets(
        self,
        namespaces: list[str] | None = None,
        *,
        stable_host_identity: bool = False,
        startup: bool = False,
    ) -> dict[str, Any]:
        candidates = namespaces or [record.namespace for record in self._load_all_records() if record.recoverable_scopes]
        restored: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in candidates:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.RESTORE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                candidate_scopes = self.scope_manager.recovery_scopes(
                    record,
                    stable_host_identity=stable_host_identity,
                    explicit_request=not startup,
                )
                if not candidate_scopes:
                    reason = "no_recoverable_scopes"
                    if record.recoverable_scopes and record.restore_requires_identity and not stable_host_identity:
                        reason = "identity_required"
                    elif record.recoverable_scopes and record.restore_requires_explicit_request and startup:
                        reason = "explicit_request_required"
                    skipped.append(
                        {
                            "namespace": namespace,
                            "reason": reason,
                            "recoverable_scopes": [scope.value for scope in record.recoverable_scopes],
                        }
                    )
                    continue

                runtime = self.transport_manager.runtime_for(namespace)
                if runtime is not None:
                    missing_scopes = [scope for scope in candidate_scopes if scope not in record.loaded_scopes]
                    if not missing_scopes:
                        skipped.append(
                            {
                                "namespace": namespace,
                                "reason": "already_restored",
                                "restored_scopes": [scope.value for scope in candidate_scopes],
                            }
                        )
                        continue

                    for scope in missing_scopes:
                        self.scope_manager.add_scope(record, scope)
                    record.transport_state = TransportState.STALE if record.stale else TransportState.CONNECTED
                    record.last_restored_at = utc_now()
                    self.store.upsert_toolset(record)
                    restored.append(
                        RestoreResult(
                            namespace=namespace,
                            restored_scopes=list(record.loaded_scopes),
                            tool_count=record.tool_count or len(runtime.tools),
                            schema_hash=record.schema_hash or inventory_hash(self._normalize_runtime_tools(namespace, runtime)),
                            reused_runtime=True,
                        ).model_dump(mode="json")
                    )
                    self._append_audit_event(
                        operation=AuditOperation.RESTORE,
                        outcome=AuditOutcome.SUCCESS,
                        namespace=namespace,
                        scope=self._audit_scope(record),
                        details={
                            "startup": startup,
                            "reused_runtime": True,
                            "restored_scopes": [scope.value for scope in record.loaded_scopes],
                        },
                    )
                    continue

                try:
                    record.transport_state = TransportState.CONNECTING
                    self.store.upsert_toolset(record)
                    runtime, tools = await self.transport_manager.open_runtime(record)
                    snapshot = self._build_snapshot(namespace, tools)
                    await self.transport_manager.swap_runtime(namespace, runtime)

                    record.previous_schema_hash = record.schema_hash
                    record.schema_hash = snapshot.schema_hash
                    record.tool_count = snapshot.tool_count
                    record.transport_state = TransportState.CONNECTED
                    record.stale = False
                    record.stale_reason = None
                    record.last_error = None
                    self._clear_health_state(record)
                    now = utc_now()
                    record.last_restored_at = now
                    record.last_refreshed_at = now
                    record.last_known_good_at = now
                    for scope in candidate_scopes:
                        self.scope_manager.add_scope(record, scope)

                    self.store.upsert_toolset(record)
                    self.store.replace_schema_snapshot(snapshot)
                    restored.append(
                        RestoreResult(
                            namespace=namespace,
                            restored_scopes=list(record.loaded_scopes),
                            tool_count=snapshot.tool_count,
                            schema_hash=snapshot.schema_hash,
                            reused_runtime=False,
                        ).model_dump(mode="json")
                    )
                    self._append_audit_event(
                        operation=AuditOperation.RESTORE,
                        outcome=AuditOutcome.SUCCESS,
                        namespace=namespace,
                        scope=self._audit_scope(record),
                        details={
                            "startup": startup,
                            "reused_runtime": False,
                            "restored_scopes": [scope.value for scope in record.loaded_scopes],
                            "schema_hash": snapshot.schema_hash,
                        },
                    )
                except Exception as exc:
                    record.transport_state = TransportState.FAILED
                    record.stale = True
                    record.stale_reason = "restore_failed"
                    record.last_error = self._error_info(
                        code="restore_failed",
                        message=str(exc),
                        namespace=namespace,
                        retryable=True,
                        details={"startup": startup},
                    )
                    self.store.upsert_toolset(record)
                    self._append_audit_event(
                        operation=AuditOperation.RESTORE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        details={
                            "startup": startup,
                            "recoverable_scopes": [scope.value for scope in record.recoverable_scopes],
                            "stale_reason": "restore_failed",
                        },
                        error=record.last_error,
                    )
                    failed.append(record.last_error.model_dump(mode="json"))

        return {"restored": restored, "skipped": skipped, "failed": failed, "error": None}

    async def clear_stale_toolsets(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        candidates = namespaces or [
            record.namespace
            for record in self._load_all_records()
            if record.stale or record.last_health_status in {HealthStatus.STALE, HealthStatus.FAILED}
        ]
        cleared: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in candidates:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.CLEAR_STALE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                stale_reason = record.stale_reason
                if (
                    not record.stale
                    and record.last_health_status not in {HealthStatus.STALE, HealthStatus.FAILED}
                    and record.last_error is None
                    and record.last_health_error is None
                ):
                    skipped.append({"namespace": namespace, "reason": "not_stale"})
                    continue

                record.stale = False
                record.stale_reason = None
                record.last_error = None
                self._clear_health_state(record)
                if record.loaded_scopes:
                    runtime = self.transport_manager.runtime_for(namespace)
                    record.transport_state = TransportState.CONNECTED if runtime is not None else TransportState.FAILED
                else:
                    record.transport_state = TransportState.INACTIVE

                self.store.upsert_toolset(record)
                self._append_audit_event(
                    operation=AuditOperation.CLEAR_STALE,
                    outcome=AuditOutcome.SUCCESS,
                    namespace=namespace,
                    details={"cleared_stale_reason": stale_reason},
                )
                cleared.append(
                    ClearStaleResult(
                        namespace=namespace,
                        transport_state=record.transport_state,
                        cleared_stale_reason=stale_reason,
                    ).model_dump(mode="json")
                )

        return {"cleared": cleared, "skipped": skipped, "failed": failed, "error": None}

    def get_toolset_status(self, namespaces: list[str] | None = None) -> dict[str, Any]:
        selected = namespaces or [record.namespace for record in self._load_all_records()]
        statuses = []
        missing = []
        for namespace in selected:
            record = self._get_record(namespace)
            if record is None:
                missing.append(
                    self._error(
                        "unknown_toolset",
                        f"Unknown toolset: {namespace}",
                        namespace=namespace,
                    )
                )
                continue
            snapshot = self.store.get_schema_snapshot(namespace)
            statuses.append(
                {
                    "registration": {
                        "namespace": record.namespace,
                        "title": record.title,
                        "description": record.description,
                        "category": record.category,
                        "tags": record.tags,
                        "aliases": record.aliases,
                        "examples": record.examples,
                        "recipes": record.recipes,
                        "activation_hint": record.activation_hint,
                        "cost_hint": record.cost_hint,
                        "latency_hint": record.latency_hint,
                        "trust_hint": record.trust_hint,
                        "transport": self._public_transport(record.transport),
                        "default_scope": record.default_scope.value,
                        "scope_policy": self._scope_policy(record),
                    },
                    "activation": {
                        "loaded_scopes": [scope.value for scope in record.loaded_scopes],
                        "transport_state": record.transport_state.value,
                        "stale": record.stale,
                        "stale_reason": record.stale_reason,
                        "in_flight_calls": self.transport_manager.in_flight_calls(namespace),
                    },
                    "recovery": {
                        "recoverable_scopes": [scope.value for scope in record.recoverable_scopes],
                        "last_restored_at": _iso(record.last_restored_at),
                        "startup_restore_enabled": self.restore_on_startup,
                        "startup_restore_eligible": bool(
                            self.scope_manager.recovery_scopes(
                                record,
                                stable_host_identity=self.stable_host_identity,
                                explicit_request=False,
                            )
                        ),
                        "explicit_restore_eligible": bool(
                            self.scope_manager.recovery_scopes(
                                record,
                                stable_host_identity=self.stable_host_identity,
                                explicit_request=True,
                            )
                        ),
                    },
                    "health": {
                        "last_checked_at": _iso(record.last_health_checked_at),
                        "last_status": record.last_health_status.value if record.last_health_status is not None else None,
                        "last_observed_schema_hash": record.last_health_observed_schema_hash,
                        "last_observed_tool_count": record.last_health_observed_tool_count,
                        "last_error": record.last_health_error.model_dump(mode="json") if record.last_health_error else None,
                    },
                    "schema": {
                        "schema_hash": record.schema_hash,
                        "previous_schema_hash": record.previous_schema_hash,
                        "last_known_good_at": _iso(record.last_known_good_at),
                        "tool_count": record.tool_count,
                        "cached_tools": [tool.model_dump(mode="json") for tool in snapshot.tools] if snapshot else [],
                        "mounted_tools": [tool.model_dump(mode="json") for tool in self.list_mounted_tools(namespace)],
                    },
                    "last_error": record.last_error.model_dump(mode="json") if record.last_error else None,
                    "recent_failure": self._recent_failure_summary(namespace),
                }
            )

        return {"count": len(statuses), "toolsets": statuses, "missing": missing, "error": None}

    async def register_toolset(
        self,
        *,
        namespace: str,
        title: str,
        description: str,
        transport: dict[str, Any],
        tags: list[str] | None = None,
        category: str = "general",
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        recipes: list[str] | None = None,
        activation_hint: str | None = None,
        cost_hint: str | None = None,
        latency_hint: str | None = None,
        trust_hint: str = "unknown",
        default_scope: str = "thread",
        supported_scopes: list[str] | None = None,
        restorable_scopes: list[str] | None = None,
        restore_requires_identity: bool = True,
        restore_requires_explicit_request: bool = True,
        auth_required: bool = False,
    ) -> dict[str, Any]:
        if not self._is_valid_namespace(namespace):
            error = self._error_info(
                "invalid_namespace",
                "Namespaces may only contain letters, numbers, underscores, and hyphens.",
                namespace=namespace or None,
            )
            self._append_audit_event(
                operation=AuditOperation.REGISTER,
                outcome=AuditOutcome.FAILURE,
                namespace=namespace or None,
                error=error,
            )
            return {
                "registered": None,
                "error": error.model_dump(mode="json"),
            }

        try:
            normalized_transport = ToolsetTransport.model_validate(transport)
        except Exception as exc:  # noqa: BLE001
            error = self._error_info(
                "invalid_transport",
                str(exc),
                namespace=namespace,
            )
            self._append_audit_event(
                operation=AuditOperation.REGISTER,
                outcome=AuditOutcome.FAILURE,
                namespace=namespace,
                error=error,
            )
            return {
                "registered": None,
                "error": error.model_dump(mode="json"),
            }

        async with self._namespace_lock(namespace):
            if self._get_record(namespace) is not None:
                error = self._error_info(
                    "toolset_exists",
                    f"Toolset already exists: {namespace}",
                    namespace=namespace,
                )
                self._append_audit_event(
                    operation=AuditOperation.REGISTER,
                    outcome=AuditOutcome.FAILURE,
                    namespace=namespace,
                    error=error,
                )
                return {
                    "registered": None,
                    "error": error.model_dump(mode="json"),
                }

            try:
                record = ToolsetRecord.model_validate(
                    {
                        "namespace": namespace,
                        "title": title,
                        "description": description,
                        "tags": self._dedupe_strings(tags or []),
                        "category": self._normalize_category(category),
                        "aliases": self._dedupe_strings(aliases or []),
                        "examples": self._dedupe_strings(examples or []),
                        "recipes": self._dedupe_strings(recipes or []),
                        "activation_hint": self._clean_optional_text(activation_hint),
                        "cost_hint": self._clean_optional_text(cost_hint),
                        "latency_hint": self._clean_optional_text(latency_hint),
                        "trust_hint": self._clean_optional_text(trust_hint) or "unknown",
                        "transport": normalized_transport.model_dump(mode="python"),
                        "default_scope": default_scope,
                        "supported_scopes": supported_scopes if supported_scopes is not None else list(Scope),
                        "restorable_scopes": restorable_scopes if restorable_scopes is not None else [],
                        "restore_requires_identity": restore_requires_identity,
                        "restore_requires_explicit_request": restore_requires_explicit_request,
                        "auth_required": auth_required,
                    }
                )
            except ValidationError as exc:
                error = self._error_info(
                    "invalid_scope_policy",
                    "The requested scope policy is invalid.",
                    namespace=namespace,
                    details={"validation_error": str(exc)},
                )
                self._append_audit_event(
                    operation=AuditOperation.REGISTER,
                    outcome=AuditOutcome.FAILURE,
                    namespace=namespace,
                    error=error,
                )
                return {
                    "registered": None,
                    "error": error.model_dump(mode="json"),
                }

            self.store.upsert_toolset(record)
            self._append_audit_event(
                operation=AuditOperation.REGISTER,
                outcome=AuditOutcome.SUCCESS,
                namespace=record.namespace,
                scope=record.default_scope,
                details={
                    "transport_kind": record.transport.kind,
                    "auth_required": record.auth_required,
                    "category": record.category,
                    "supported_scopes": [scope.value for scope in record.supported_scopes],
                    "restorable_scopes": [scope.value for scope in record.restorable_scopes],
                },
            )
            return {
                "registered": {
                    "namespace": record.namespace,
                    "title": record.title,
                    "description": record.description,
                    "category": record.category,
                    "tags": record.tags,
                    "aliases": record.aliases,
                    "examples": record.examples,
                    "recipes": record.recipes,
                    "activation_hint": record.activation_hint,
                    "cost_hint": record.cost_hint,
                    "latency_hint": record.latency_hint,
                    "trust_hint": record.trust_hint,
                    "transport": self._public_transport(record.transport),
                    "default_scope": record.default_scope.value,
                    "scope_policy": self._scope_policy(record),
                    "auth_required": record.auth_required,
                },
                "error": None,
            }

    async def unregister_toolsets(
        self,
        namespaces: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        unregistered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in namespaces:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.UNREGISTER,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                if record.loaded_scopes and not force:
                    error = self._error_info(
                        "toolset_active",
                        f"Cannot unregister active toolset: {namespace}",
                        namespace=namespace,
                        retryable=True,
                        details={"loaded_scopes": [scope.value for scope in record.loaded_scopes]},
                    )
                    self._append_audit_event(
                        operation=AuditOperation.UNREGISTER,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                disconnected_runtime = False
                if record.loaded_scopes and force:
                    in_flight = self.transport_manager.in_flight_calls(namespace)
                    if in_flight:
                        error = self._error_info(
                            "in_flight_calls",
                            f"Cannot force-unregister {namespace} while {in_flight} calls are in flight.",
                            namespace=namespace,
                            retryable=True,
                            details={"in_flight_calls": in_flight},
                        )
                        self._append_audit_event(
                            operation=AuditOperation.UNREGISTER,
                            outcome=AuditOutcome.FAILURE,
                            namespace=namespace,
                            error=error,
                        )
                        failed.append(error.model_dump(mode="json"))
                        continue
                    await self.transport_manager.disconnect(namespace)
                    disconnected_runtime = True
                    skipped.append(
                        {
                            "namespace": namespace,
                            "reason": "disconnected_before_unregistration",
                        }
                    )

                self.store.delete_schema_snapshot(namespace)
                self.store.delete_previous_schema_snapshot(namespace)
                self.store.delete_toolset(namespace)
                self._namespace_locks.pop(namespace, None)
                self._append_audit_event(
                    operation=AuditOperation.UNREGISTER,
                    outcome=AuditOutcome.SUCCESS,
                    namespace=namespace,
                    details={
                        "force": force,
                        "disconnected_runtime": disconnected_runtime,
                    },
                )
                unregistered.append({"namespace": namespace})

        return {"unregistered": unregistered, "skipped": skipped, "failed": failed, "error": None}

    async def activate_toolsets(
        self,
        namespaces: list[str],
        scope: str = "thread",
        if_not_loaded: bool = True,
    ) -> dict[str, Any]:
        try:
            scope_value = Scope(scope)
        except ValueError:
            error = self._error_info("invalid_scope", f"Unsupported scope: {scope}")
            self._append_audit_event(
                operation=AuditOperation.ACTIVATE,
                outcome=AuditOutcome.FAILURE,
                scope=None,
                error=error,
            )
            return {"activated": [], "skipped": [], "failed": [], "error": error.model_dump(mode="json")}

        activated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in namespaces:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.ACTIVATE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                if not self.scope_manager.supports_scope(record, scope_value):
                    error = self._error_info(
                        "unsupported_scope",
                        f"Toolset {namespace} does not support scope: {scope_value.value}",
                        namespace=namespace,
                        details={"supported_scopes": [scope.value for scope in record.supported_scopes]},
                    )
                    self._append_audit_event(
                        operation=AuditOperation.ACTIVATE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                runtime = self.transport_manager.runtime_for(namespace)
                if runtime is not None and record.loaded_scopes and scope_value not in record.loaded_scopes:
                    self.scope_manager.add_scope(record, scope_value)
                    self.scope_manager.add_recoverable_scope(record, scope_value)
                    record.transport_state = TransportState.STALE if record.stale else TransportState.CONNECTED
                    record.last_activated_at = utc_now()
                    self.store.upsert_toolset(record)
                    self._append_audit_event(
                        operation=AuditOperation.ACTIVATE,
                        outcome=AuditOutcome.SUCCESS,
                        namespace=namespace,
                        scope=scope_value,
                        details={
                            "tool_count": record.tool_count or len(runtime.tools),
                            "schema_hash": record.schema_hash,
                            "reused_runtime": True,
                            "loaded_scopes": [item.value for item in record.loaded_scopes],
                        },
                    )
                    activated.append(
                        ActivationResult(
                            namespace=namespace,
                            scope=scope_value,
                            tool_count=record.tool_count or len(runtime.tools),
                            schema_hash=record.schema_hash or inventory_hash(self._normalize_runtime_tools(namespace, runtime)),
                        ).model_dump(mode="json")
                    )
                    continue

                if if_not_loaded and scope_value in record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "already_active", "scope": scope_value.value})
                    continue

                try:
                    record.transport_state = TransportState.CONNECTING
                    self.store.upsert_toolset(record)
                    runtime, tools = await self.transport_manager.open_runtime(record)
                    snapshot = self._build_snapshot(namespace, tools)
                    await self.transport_manager.swap_runtime(namespace, runtime)

                    record.previous_schema_hash = record.schema_hash
                    record.schema_hash = snapshot.schema_hash
                    record.tool_count = snapshot.tool_count
                    record.transport_state = TransportState.CONNECTED
                    record.stale = False
                    record.stale_reason = None
                    record.last_error = None
                    self._clear_health_state(record)
                    now = utc_now()
                    record.last_activated_at = now
                    record.last_refreshed_at = now
                    record.last_known_good_at = now
                    self.scope_manager.add_scope(record, scope_value)
                    self.scope_manager.add_recoverable_scope(record, scope_value)

                    self.store.upsert_toolset(record)
                    self.store.replace_schema_snapshot(snapshot)
                    self._append_audit_event(
                        operation=AuditOperation.ACTIVATE,
                        outcome=AuditOutcome.SUCCESS,
                        namespace=namespace,
                        scope=scope_value,
                        details={
                            "tool_count": snapshot.tool_count,
                            "schema_hash": snapshot.schema_hash,
                        },
                    )
                    activated.append(
                        ActivationResult(
                            namespace=namespace,
                            scope=scope_value,
                            tool_count=snapshot.tool_count,
                            schema_hash=snapshot.schema_hash,
                        ).model_dump(mode="json")
                    )
                except Exception as exc:
                    record.transport_state = TransportState.FAILED
                    record.last_error = self._error_info(
                        code="transport_failed",
                        message=str(exc),
                        namespace=namespace,
                        retryable=True,
                    )
                    self.store.upsert_toolset(record)
                    self._append_audit_event(
                        operation=AuditOperation.ACTIVATE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=record.last_error,
                    )
                    failed.append(record.last_error.model_dump(mode="json"))

        return {"activated": activated, "skipped": skipped, "failed": failed, "error": None}

    async def refresh_toolsets(
        self,
        namespaces: list[str] | None = None,
        if_stale: bool = True,
        scope: str | None = None,
    ) -> dict[str, Any]:
        scope_value = None
        if scope is not None:
            try:
                scope_value = Scope(scope)
            except ValueError:
                error = self._error_info("invalid_scope", f"Unsupported scope: {scope}")
                self._append_audit_event(
                    operation=AuditOperation.REFRESH,
                    outcome=AuditOutcome.FAILURE,
                    error=error,
                )
                return {"refreshed": [], "skipped": [], "failed": [], "error": error.model_dump(mode="json")}

        candidates = namespaces or [record.namespace for record in self._load_all_records()]
        refreshed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in candidates:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.REFRESH,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue
                if not record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "not_active"})
                    continue
                if scope_value is not None and scope_value not in record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "scope_not_active", "scope": scope_value.value})
                    continue
                if if_stale and not record.stale and record.schema_hash is not None:
                    skipped.append({"namespace": namespace, "reason": "not_stale"})
                    continue

                previous_snapshot = self.store.get_schema_snapshot(namespace)
                previous_tools = previous_snapshot.tools if previous_snapshot else []
                old_hash = previous_snapshot.schema_hash if previous_snapshot else None
                try:
                    runtime, tools = await self.transport_manager.open_runtime(record)
                    snapshot = self._build_snapshot(namespace, tools)
                    diff = diff_toolsets(namespace, previous_tools, snapshot.tools)
                    diff.old_schema_hash = old_hash
                    diff.new_schema_hash = snapshot.schema_hash
                    await self.transport_manager.swap_runtime(namespace, runtime)

                    record.previous_schema_hash = old_hash
                    record.schema_hash = snapshot.schema_hash
                    record.tool_count = snapshot.tool_count
                    record.transport_state = TransportState.CONNECTED
                    record.stale = False
                    record.stale_reason = None
                    record.last_error = None
                    self._clear_health_state(record)
                    now = utc_now()
                    record.last_refreshed_at = now
                    record.last_known_good_at = now

                    self.store.upsert_toolset(record)
                    self.store.replace_schema_snapshot(snapshot)
                    self._append_audit_event(
                        operation=AuditOperation.REFRESH,
                        outcome=AuditOutcome.SUCCESS,
                        namespace=namespace,
                        scope=self._audit_scope(record, scope_value),
                        details={
                            "old_schema_hash": old_hash,
                            "new_schema_hash": snapshot.schema_hash,
                            "added_tools": diff.added_tools,
                            "removed_tools": diff.removed_tools,
                            "changed_tools": diff.changed_tools,
                        },
                    )
                    refreshed.append(diff.model_dump(mode="json"))
                except Exception as exc:
                    record.transport_state = (
                        TransportState.STALE if self.transport_manager.has_connection(namespace) else TransportState.FAILED
                    )
                    record.stale = True
                    record.stale_reason = "refresh_failed"
                    record.last_error = self._error_info(
                        code="refresh_failed",
                        message=str(exc),
                        namespace=namespace,
                        retryable=True,
                        details={"stale_reason": "refresh_failed"},
                    )
                    self.store.upsert_toolset(record)
                    self._append_audit_event(
                        operation=AuditOperation.REFRESH,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=self._audit_scope(record, scope_value),
                        details={
                            "old_schema_hash": old_hash,
                            "stale_reason": "refresh_failed",
                        },
                        error=record.last_error,
                    )
                    failed.append(record.last_error.model_dump(mode="json"))

        return {"refreshed": refreshed, "skipped": skipped, "failed": failed, "error": None}

    async def deactivate_toolsets(
        self,
        namespaces: list[str],
        scope: str = "thread",
        force: bool = False,
    ) -> dict[str, Any]:
        try:
            scope_value = Scope(scope)
        except ValueError:
            error = self._error_info("invalid_scope", f"Unsupported scope: {scope}")
            self._append_audit_event(
                operation=AuditOperation.DEACTIVATE,
                outcome=AuditOutcome.FAILURE,
                error=error,
            )
            return {"deactivated": [], "skipped": [], "failed": [], "error": error.model_dump(mode="json")}

        deactivated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for namespace in namespaces:
            async with self._namespace_lock(namespace):
                record = self._get_record(namespace)
                if record is None:
                    error = self._error_info("unknown_toolset", f"Unknown toolset: {namespace}", namespace=namespace)
                    self._append_audit_event(
                        operation=AuditOperation.DEACTIVATE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue
                if scope_value not in record.loaded_scopes:
                    skipped.append({"namespace": namespace, "reason": "not_active", "scope": scope_value.value})
                    continue

                in_flight = self.transport_manager.in_flight_calls(namespace)
                if in_flight and not force:
                    error = self._error_info(
                        "in_flight_calls",
                        f"Cannot deactivate {namespace} while {in_flight} calls are in flight.",
                        namespace=namespace,
                        retryable=True,
                        details={"in_flight_calls": in_flight},
                    )
                    self._append_audit_event(
                        operation=AuditOperation.DEACTIVATE,
                        outcome=AuditOutcome.FAILURE,
                        namespace=namespace,
                        scope=scope_value,
                        error=error,
                    )
                    failed.append(error.model_dump(mode="json"))
                    continue

                self.scope_manager.remove_scope(record, scope_value)
                self.scope_manager.remove_recoverable_scope(record, scope_value)
                if not record.loaded_scopes:
                    await self.transport_manager.disconnect(namespace)
                    record.transport_state = TransportState.INACTIVE
                    record.stale = False
                    record.stale_reason = None
                    record.last_error = None
                self.store.upsert_toolset(record)
                self._append_audit_event(
                    operation=AuditOperation.DEACTIVATE,
                    outcome=AuditOutcome.SUCCESS,
                    namespace=namespace,
                    scope=scope_value,
                    details={
                        "force": force,
                        "remaining_scopes": [item.value for item in record.loaded_scopes],
                    },
                )
                deactivated.append(
                    {
                        "namespace": namespace,
                        "scope": scope_value.value,
                        "remaining_scopes": [item.value for item in record.loaded_scopes],
                    }
                )

        return {"deactivated": deactivated, "skipped": skipped, "failed": failed, "error": None}

    async def mark_toolset_stale(self, namespace: str, reason: str) -> bool:
        async with self._namespace_lock(namespace):
            record = self._get_record(namespace)
            if record is None:
                return False
            record.stale = True
            record.stale_reason = reason
            record.transport_state = TransportState.STALE
            self.store.upsert_toolset(record)
            return True

    def note_call_started(self, namespace: str) -> None:
        self.transport_manager.note_call_started(namespace)

    def note_call_finished(self, namespace: str) -> None:
        self.transport_manager.note_call_finished(namespace)

    def list_mounted_tools(self, namespace: str | None = None) -> list[MountedTool]:
        mounted_tools: list[MountedTool] = []
        namespaces = [namespace] if namespace is not None else self.transport_manager.mounted_namespaces()
        for mounted_namespace in namespaces:
            runtime = self.transport_manager.runtime_for(mounted_namespace)
            if runtime is None:
                continue
            for tool in runtime.tools:
                mounted_tools.append(
                    MountedTool(
                        namespace=mounted_namespace,
                        mounted_name=self._mounted_tool_name(mounted_namespace, tool.name),
                        source_name=tool.name,
                        title=tool.title,
                        description=tool.description,
                        input_schema=tool.inputSchema or {},
                        output_schema=tool.outputSchema,
                    )
                )
        mounted_tools.sort(key=lambda item: item.mounted_name)
        return mounted_tools

    def list_mounted_tool_definitions(self) -> list[types.Tool]:
        definitions: list[types.Tool] = []
        for mounted in self.list_mounted_tools():
            definitions.append(
                types.Tool(
                    name=mounted.mounted_name,
                    title=mounted.title,
                    description=mounted.description or "",
                    inputSchema=mounted.input_schema,
                    outputSchema=mounted.output_schema,
                )
            )
        return definitions

    def is_mounted_tool(self, mounted_name: str) -> bool:
        return self._resolve_mounted_tool(mounted_name) is not None

    async def call_mounted_tool(self, mounted_name: str, arguments: dict[str, Any] | None = None) -> types.CallToolResult:
        mounted = self._resolve_mounted_tool(mounted_name)
        if mounted is None:
            raise ValueError(f"Unknown mounted tool: {mounted_name}")

        async with self._namespace_lock(mounted.namespace):
            mounted = self._resolve_mounted_tool(mounted_name)
            if mounted is None:
                raise ValueError(f"Unknown mounted tool: {mounted_name}")
            self.note_call_started(mounted.namespace)

        try:
            return await self.transport_manager.call_tool(
                mounted.namespace,
                mounted.source_name,
                arguments or {},
            )
        except Exception as exc:
            await self.transport_manager.disconnect(mounted.namespace)
            error_code = "call_timeout" if isinstance(exc, TimeoutError) else "transport_failed"
            stale_reason = "call_timeout" if error_code == "call_timeout" else "call_failed"
            error = self._record_runtime_failure(
                mounted.namespace,
                error_code,
                str(exc),
                stale_reason=stale_reason,
                details={"mounted_tool": mounted_name},
            )
            self._append_audit_event(
                operation=AuditOperation.CALL,
                outcome=AuditOutcome.FAILURE,
                namespace=mounted.namespace,
                scope=self._audit_scope(self._get_record(mounted.namespace)),
                details={
                    "mounted_tool": mounted_name,
                    "stale_reason": stale_reason,
                },
                error=error,
            )
            raise
        finally:
            async with self._namespace_lock(mounted.namespace):
                self.note_call_finished(mounted.namespace)

    async def run_tool_batch(
        self,
        steps: list[BatchStep],
        final_step: str | None = None,
        continue_on_error: bool = False,
    ) -> BatchRunResult:
        if not steps:
            return BatchRunResult(
                success=False,
                error=ErrorInfo(code="invalid_batch", message="run_tool_batch requires at least one step"),
                step_count=0,
                completed_steps=0,
            )

        seen_ids: set[str] = set()
        for step in steps:
            if step.id in seen_ids:
                return BatchRunResult(
                    success=False,
                    error=ErrorInfo(
                        code="invalid_batch",
                        message=f"Duplicate batch step id: {step.id}",
                        details={"step_id": step.id},
                    ),
                    step_count=len(steps),
                    completed_steps=0,
                )
            seen_ids.add(step.id)

        results: list[BatchStepResult] = []
        results_by_id: dict[str, BatchStepResult] = {}
        stopped_on_error = False

        for step in steps:
            try:
                resolved_arguments = self._resolve_batch_value(step.arguments, results_by_id)
                if not isinstance(resolved_arguments, dict):
                    raise ValueError(f"Batch step arguments must resolve to an object for step {step.id}")

                if not self.is_mounted_tool(step.tool):
                    raise ValueError(f"Batch step {step.id} references unknown mounted tool: {step.tool}")

                call_result = await self.call_mounted_tool(step.tool, resolved_arguments)
                data, text = self._normalize_call_result(call_result)
                step_result = BatchStepResult(
                    id=step.id,
                    tool=step.tool,
                    resolved_arguments=self._redacted_argument_value(resolved_arguments),
                    data=data,
                    text=text,
                    is_error=bool(call_result.isError),
                    error_message=self._error_text_from_result(call_result),
                )
            except Exception as exc:
                step_result = BatchStepResult(
                    id=step.id,
                    tool=step.tool,
                    resolved_arguments={},
                    is_error=True,
                    error_message=str(exc),
                )

            results.append(step_result)
            results_by_id[step.id] = step_result

            if step_result.is_error and not continue_on_error:
                stopped_on_error = True
                break

        selected_step_id = final_step or (results[-1].id if results else None)
        selected_result = results_by_id.get(selected_step_id) if selected_step_id else None
        first_error = next((result for result in results if result.is_error), None)
        return BatchRunResult(
            success=first_error is None,
            error=(
                ErrorInfo(
                    code="step_failed",
                    message=f"Batch step {first_error.id} failed: {first_error.error_message or 'unknown error'}",
                    details={"step_id": first_error.id, "tool": first_error.tool},
                )
                if first_error is not None
                else None
            ),
            step_count=len(steps),
            completed_steps=len(results),
            stopped_on_error=stopped_on_error,
            final_step=selected_step_id,
            final_data=selected_result.data if selected_result else None,
            final_text=selected_result.text if selected_result else None,
            results=results,
        )

    async def run_tool_program(
        self,
        program: str,
        *,
        initial_context: dict[str, Any] | None = None,
        result_variable: str = "result",
        return_variables: list[str] | None = None,
        return_contract_summaries: list[str] | None = None,
    ) -> ProgramRunResult:
        runtime = ToolProgramRuntime(self.call_mounted_tool)
        result = await runtime.run(
            program,
            initial_context=initial_context,
            result_variable=result_variable,
            return_variables=return_variables,
        )
        if return_contract_summaries:
            descriptions = self.describe_mounted_tools(mounted_names=return_contract_summaries)
            result.contract_summaries = [
                MountedToolDescription.model_validate(item)
                for item in descriptions["descriptions"]
            ]
            result.missing_contract_summaries = list(descriptions["missing"])
        return result

    async def shutdown(self) -> None:
        await self.transport_manager.shutdown()

    def _build_snapshot(self, namespace: str, tools: list[ToolSchema]) -> SchemaSnapshot:
        return SchemaSnapshot(
            namespace=namespace,
            schema_hash=inventory_hash(tools),
            tool_count=len(tools),
            tools=tools,
            captured_at=utc_now(),
        )

    def _normalize_runtime_tools(self, namespace: str, runtime: Any) -> list[ToolSchema]:
        return validate_tool_inventory(namespace, list(runtime.tools))

    def _diff_contracts(self, selected: list[str], *, source: str) -> dict[str, Any]:
        result = ContractDiffResult(
            count=0,
            diffs=[self._build_contract_diff(namespace, source=source) for namespace in selected],
        )
        result.count = len(result.diffs)
        payload = result.model_dump(mode="json")
        payload["error"] = None
        return payload

    def _contract_source_details(self, namespace: str, *, source: str) -> dict[str, Any]:
        record = self._get_record(namespace)
        snapshot = self.store.get_schema_snapshot(namespace)
        runtime = self.transport_manager.runtime_for(namespace)

        cached_tools = snapshot.tools if snapshot is not None else []
        mounted_tools = [normalize_tool(tool) for tool in runtime.tools] if runtime is not None else []
        has_cached = snapshot is not None
        has_mounted = runtime is not None
        source_available = has_cached if source == "cached" else has_mounted
        source_tools = cached_tools if source == "cached" else mounted_tools

        schema_hash = None
        if source == "cached" and snapshot is not None:
            schema_hash = snapshot.schema_hash
        elif source == "mounted" and mounted_tools:
            schema_hash = inventory_hash(mounted_tools)

        return {
            "record": record,
            "snapshot": snapshot,
            "runtime": runtime,
            "cached_tools": cached_tools,
            "mounted_tools": mounted_tools,
            "has_cached": has_cached,
            "has_mounted": has_mounted,
            "source_available": source_available,
            "source_tools": source_tools,
            "schema_hash": schema_hash,
            "captured_at": snapshot.captured_at if source == "cached" and snapshot is not None else None,
        }

    def _build_contract_inspection(self, namespace: str, *, source: str) -> ToolsetContractInspection:
        details = self._contract_source_details(namespace, source=source)
        record = details["record"]

        return ToolsetContractInspection(
            namespace=namespace,
            source=source,
            availability=ContractAvailability(
                registered=record is not None,
                cached=details["has_cached"],
                mounted=details["has_mounted"],
                stale=record.stale if record is not None else False,
                missing=not details["source_available"],
            ),
            transport_state=record.transport_state if record is not None else None,
            stale_reason=record.stale_reason if record is not None else None,
            schema_hash=details["schema_hash"],
            previous_schema_hash=record.previous_schema_hash if record is not None else None,
            tool_count=len(details["source_tools"]) if details["source_available"] else 0,
            captured_at=details["captured_at"],
            last_known_good_at=record.last_known_good_at if record is not None else None,
            tools=[
                self._summarize_tool_contract(tool, namespace=namespace if source == "mounted" else None)
                for tool in (details["source_tools"] if details["source_available"] else [])
            ],
        )

    def _build_contract_diff(self, namespace: str, *, source: str) -> ToolsetContractDiff:
        inspection = self._build_contract_inspection(namespace, source=source)
        previous_snapshot = self.store.get_previous_schema_snapshot(namespace)
        previous_tools = previous_snapshot.tools if previous_snapshot is not None else []
        current_tools: list[ToolSchema] = []
        if inspection.availability.missing is False:
            details = self._contract_source_details(namespace, source=source)
            current_tools = details["source_tools"]

        previous_schema_hash = (
            previous_snapshot.schema_hash
            if previous_snapshot is not None
            else inspection.previous_schema_hash
        )
        diff_available = inspection.availability.missing is False and previous_snapshot is not None

        added_tools: list[str] = []
        removed_tools: list[str] = []
        changed_tools: list[str] = []
        changes: list[ToolContractDelta] = []

        if diff_available:
            diff = diff_toolsets(namespace, previous_tools, current_tools)
            previous_by_name = {tool.name: tool for tool in previous_tools}
            current_by_name = {tool.name: tool for tool in current_tools}
            added_tools = diff.added_tools
            removed_tools = diff.removed_tools
            changed_tools = diff.changed_tools

            for name in added_tools:
                changes.append(
                    ToolContractDelta(
                        change="added",
                        name=name,
                        mounted_name=self._mounted_tool_name(namespace, name) if source == "mounted" else None,
                        current_summary=self._summarize_tool_contract(
                            current_by_name[name],
                            namespace=namespace if source == "mounted" else None,
                        ),
                    )
                )
            for name in removed_tools:
                changes.append(
                    ToolContractDelta(
                        change="removed",
                        name=name,
                        mounted_name=self._mounted_tool_name(namespace, name) if source == "mounted" else None,
                        previous_summary=self._summarize_tool_contract(previous_by_name[name]),
                    )
                )
            for name in changed_tools:
                changes.append(
                    ToolContractDelta(
                        change="changed",
                        name=name,
                        mounted_name=self._mounted_tool_name(namespace, name) if source == "mounted" else None,
                        previous_summary=self._summarize_tool_contract(previous_by_name[name]),
                        current_summary=self._summarize_tool_contract(
                            current_by_name[name],
                            namespace=namespace if source == "mounted" else None,
                        ),
                    )
                )

        return ToolsetContractDiff(
            namespace=namespace,
            source=source,
            availability=inspection.availability,
            previous_available=previous_snapshot is not None,
            diff_available=diff_available,
            transport_state=inspection.transport_state,
            stale_reason=inspection.stale_reason,
            current_schema_hash=inspection.schema_hash,
            previous_schema_hash=previous_schema_hash,
            current_tool_count=inspection.tool_count,
            previous_tool_count=len(previous_tools),
            current_captured_at=inspection.captured_at,
            previous_captured_at=previous_snapshot.captured_at if previous_snapshot is not None else None,
            has_changes=bool(added_tools or removed_tools or changed_tools),
            added_tools=added_tools,
            removed_tools=removed_tools,
            changed_tools=changed_tools,
            changes=changes,
        )

    def _recent_failure_summary(self, namespace: str) -> dict[str, Any] | None:
        failure_events = self.store.list_audit_events(
            namespace=namespace,
            outcome=AuditOutcome.FAILURE,
            limit=self.store.audit_retention_limit,
        )
        if not failure_events:
            return None

        latest = failure_events[-1]
        return {
            "count": len(failure_events),
            "latest_at": _iso(latest.timestamp),
            "latest_operation": latest.operation.value,
            "scope": latest.scope.value if latest.scope is not None else None,
            "registration_present": latest.registration_present,
            "error": latest.error.model_dump(mode="json") if latest.error else None,
            "details": latest.details,
        }

    def _record_health_observation(
        self,
        namespace: str,
        *,
        status: HealthStatus,
        observed_schema_hash: str | None,
        observed_tool_count: int | None,
        error: ErrorInfo | None,
    ) -> HealthCheckResult:
        record = self._get_record(namespace)
        if record is None:
            raise ValueError(f"Unknown toolset: {namespace}")

        now = utc_now()
        record.last_health_checked_at = now
        record.last_health_status = status
        record.last_health_observed_schema_hash = observed_schema_hash
        record.last_health_observed_tool_count = observed_tool_count
        record.last_health_error = error
        if status is HealthStatus.HEALTHY:
            record.transport_state = TransportState.CONNECTED if self.transport_manager.has_connection(namespace) else record.transport_state
        self.store.upsert_toolset(record)
        return HealthCheckResult(
            namespace=namespace,
            status=status,
            transport_state=record.transport_state,
            loaded_scopes=list(record.loaded_scopes),
            stale=record.stale,
            stale_reason=record.stale_reason,
            cached_schema_hash=record.schema_hash,
            observed_schema_hash=observed_schema_hash,
            observed_tool_count=observed_tool_count,
            last_checked_at=now,
            error=error,
        )

    @staticmethod
    def _clear_health_state(record: ToolsetRecord) -> None:
        record.last_health_status = None
        record.last_health_checked_at = None
        record.last_health_observed_schema_hash = None
        record.last_health_observed_tool_count = None
        record.last_health_error = None

    def _summarize_tool_contract(self, tool: ToolSchema, *, namespace: str | None = None) -> ToolContractSummary:
        return ToolContractSummary(
            name=tool.name,
            mounted_name=self._mounted_tool_name(namespace, tool.name) if namespace is not None else None,
            title=tool.title,
            description=tool.description,
            tool_hash=tool.tool_hash,
            input_schema=self._summarize_schema_shape(tool.input_schema),
            output_schema=self._summarize_schema_shape(tool.output_schema) if tool.output_schema is not None else None,
        )

    def _build_mounted_tool_description(self, mounted: MountedTool) -> MountedToolDescription:
        normalized = normalize_tool(
            types.Tool(
                name=mounted.source_name,
                title=mounted.title,
                description=mounted.description or "",
                inputSchema=mounted.input_schema,
                outputSchema=mounted.output_schema,
            )
        )
        input_summary = self._summarize_schema_shape(normalized.input_schema)
        return MountedToolDescription(
            namespace=mounted.namespace,
            mounted_name=mounted.mounted_name,
            source_name=mounted.source_name,
            title=mounted.title,
            description=mounted.description,
            tool_hash=normalized.tool_hash,
            input_schema=input_summary,
            output_schema=(
                self._summarize_schema_shape(normalized.output_schema)
                if normalized.output_schema is not None
                else None
            ),
            required_inputs=list(input_summary.required_properties),
        )

    @staticmethod
    def _summarize_schema_shape(schema: dict[str, Any] | None) -> SchemaShapeSummary:
        if not isinstance(schema, dict):
            return SchemaShapeSummary()

        properties = schema.get("properties")
        property_names = sorted(properties.keys()) if isinstance(properties, dict) else []
        required = schema.get("required")
        required_properties = sorted(item for item in required if isinstance(item, str)) if isinstance(required, list) else []
        items = schema.get("items")
        item_type = items.get("type") if isinstance(items, dict) else None
        additional_properties = schema.get("additionalProperties")

        return SchemaShapeSummary(
            root_type=schema.get("type") if isinstance(schema.get("type"), str) else None,
            property_names=property_names,
            required_properties=required_properties,
            property_count=len(property_names),
            item_type=item_type if isinstance(item_type, str) else None,
            has_additional_properties=additional_properties if isinstance(additional_properties, bool) else None,
        )

    @staticmethod
    def _agent_discovery_fields() -> list[str]:
        return [
            "namespace",
            "title",
            "description",
            "category",
            "tags",
            "aliases",
            "examples",
            "recipes",
            "activation_hint",
        ]

    @staticmethod
    def _discovery_terms(value: str) -> list[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "for",
            "from",
            "i",
            "in",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "this",
            "to",
            "use",
            "with",
            "you",
        }
        normalized = "".join(
            character.lower() if character.isalnum() or character in {"_", "-"} else " "
            for character in value
        )
        return [term for term in normalized.split() if term not in stop_words]

    @staticmethod
    def _clean_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _dedupe_strings(values: Any) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
        return result

    @staticmethod
    def _normalize_category(value: str | None) -> str:
        cleaned = (value or "general").strip().lower().replace(" ", "_")
        normalized = "".join(character for character in cleaned if character.isalnum() or character in {"_", "-"})
        return normalized or "general"

    def _toolset_summary(self, record: ToolsetRecord) -> ToolsetSummary:
        return ToolsetSummary(
            namespace=record.namespace,
            title=record.title,
            description=record.description,
            category=record.category,
            tags=record.tags,
            aliases=record.aliases,
            examples=record.examples[:3],
            recipes=record.recipes[:3],
            activation_hint=record.activation_hint,
            cost_hint=record.cost_hint,
            latency_hint=record.latency_hint,
            trust_hint=record.trust_hint,
            loaded=bool(record.loaded_scopes),
            stale=record.stale,
            transport_state=record.transport_state,
            tool_count=record.tool_count,
            auth_required=record.auth_required,
        )

    def _score_record_for_terms(self, record: ToolsetRecord, terms: list[str]) -> tuple[int, list[str]]:
        if not terms:
            return 0, []

        weighted_fields: list[tuple[str, str, int]] = [
            ("namespace", record.namespace, 4),
            ("title", record.title, 4),
            ("category", record.category, 5),
            ("description", record.description, 3),
            ("activation_hint", record.activation_hint or "", 3),
            ("tags", " ".join(record.tags), 4),
            ("aliases", " ".join(record.aliases), 4),
            ("examples", " ".join(record.examples), 2),
            ("recipes", " ".join(record.recipes), 3),
        ]

        score = 0
        reasons: list[str] = []
        for term in terms:
            for field, value, weight in weighted_fields:
                if term not in value.lower():
                    continue
                score += weight
                reason = f"matched {field}: {term}"
                if reason not in reasons:
                    reasons.append(reason)

        if score > 0:
            if record.loaded_scopes:
                reasons.append("already loaded")
                score += 1
            if record.stale:
                reasons.append("currently stale")
                score -= 2
        return max(score, 0), reasons

    @staticmethod
    def _public_transport(transport: ToolsetTransport) -> dict[str, Any]:
        return {
            "kind": transport.kind,
            "command": transport.command,
            "cwd": transport.cwd,
            "has_args": bool(transport.args),
            "arg_count": len(transport.args),
            "args_values_redacted": bool(transport.args),
            "has_env": bool(transport.env),
            "env_keys": sorted(transport.env.keys()),
            "env_values_redacted": bool(transport.env),
        }

    @staticmethod
    def _redacted_argument_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(key): ToolboxService._redacted_argument_value(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [ToolboxService._redacted_argument_value(item) for item in value]
        return "[redacted]"

    def _mounted_tool_name(self, namespace: str, source_name: str) -> str:
        return f"{namespace}.{source_name}"

    def _mounted_tool_missing_reason(self, mounted_name: str, active_namespaces: list[str]) -> str:
        namespace, separator, _ = mounted_name.partition(".")
        if not separator:
            return "invalid_mounted_name"
        if self._get_record(namespace) is None:
            return "unknown_toolset"
        if namespace not in active_namespaces:
            return "namespace_not_mounted"
        return "tool_not_mounted"

    def _resolve_mounted_tool(self, mounted_name: str) -> MountedTool | None:
        mounted_namespaces = sorted(self.transport_manager.mounted_namespaces(), key=len, reverse=True)
        for namespace in mounted_namespaces:
            prefix = f"{namespace}."
            if not mounted_name.startswith(prefix):
                continue

            source_name = mounted_name[len(prefix) :]
            runtime = self.transport_manager.runtime_for(namespace)
            if runtime is None:
                continue
            for tool in runtime.tools:
                if tool.name == source_name:
                    return MountedTool(
                        namespace=namespace,
                        mounted_name=mounted_name,
                        source_name=source_name,
                        title=tool.title,
                        description=tool.description,
                        input_schema=tool.inputSchema or {},
                        output_schema=tool.outputSchema,
                    )
        return None

    def _record_runtime_failure(
        self,
        namespace: str,
        code: str,
        message: str,
        *,
        stale_reason: str,
        details: dict[str, Any] | None = None,
    ) -> ErrorInfo | None:
        record = self._get_record(namespace)
        if record is None:
            return None
        record.transport_state = TransportState.STALE if self.transport_manager.has_connection(namespace) else TransportState.FAILED
        record.stale = True
        record.stale_reason = stale_reason
        record.last_error = self._error_info(
            code=code,
            message=message,
            namespace=namespace,
            retryable=True,
            details=details,
        )
        self.store.upsert_toolset(record)
        return record.last_error

    def _resolve_batch_value(
        self,
        value: Any,
        results_by_id: dict[str, BatchStepResult],
    ) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"$from"}:
                reference = value["$from"]
                if not isinstance(reference, str):
                    raise ValueError("Batch reference values must be strings")
                return self._extract_batch_reference(reference, results_by_id)
            return {key: self._resolve_batch_value(item, results_by_id) for key, item in value.items()}

        if isinstance(value, list):
            return [self._resolve_batch_value(item, results_by_id) for item in value]

        return value

    def _extract_batch_reference(
        self,
        reference: str,
        results_by_id: dict[str, BatchStepResult],
    ) -> Any:
        tokens = [token for token in reference.split(".") if token]
        if not tokens:
            raise ValueError("Batch reference cannot be empty")

        step_id = tokens[0]
        step_result = results_by_id.get(step_id)
        if step_result is None:
            raise ValueError(f"Batch reference points to an unknown step: {step_id}")
        if step_result.is_error:
            raise ValueError(f"Batch reference points to a failed step: {step_id}")

        current: Any = step_result.data if step_result.data is not None else step_result.text
        for token in tokens[1:]:
            if isinstance(current, dict):
                if token not in current:
                    raise ValueError(f"Batch reference '{reference}' is missing key '{token}'")
                current = current[token]
                continue
            if isinstance(current, list):
                if not token.isdigit():
                    raise ValueError(f"Batch reference '{reference}' expected a numeric list index, got '{token}'")
                index = int(token)
                if index >= len(current):
                    raise ValueError(f"Batch reference '{reference}' index '{index}' is out of range")
                current = current[index]
                continue
            raise ValueError(f"Batch reference '{reference}' cannot descend into '{token}'")

        return current

    def _normalize_call_result(self, result: types.CallToolResult) -> tuple[Any | None, str | None]:
        text = self._joined_text_content(result)
        data = result.structuredContent
        if data is None and text is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
        return data, text

    def _joined_text_content(self, result: types.CallToolResult) -> str | None:
        text_blocks = [block.text for block in result.content if isinstance(block, types.TextContent)]
        if not text_blocks:
            return None
        return "\n".join(text_blocks)

    def _error_text_from_result(self, result: types.CallToolResult) -> str | None:
        if not result.isError:
            return None
        return self._joined_text_content(result)


def _merged_pythonpath(cwd: str) -> str:
    parts = [cwd]
    existing = os.getenv("PYTHONPATH")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

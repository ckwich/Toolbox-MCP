from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Scope(str, Enum):
    THREAD = "thread"
    SESSION = "session"
    GLOBAL = "global"


_SCOPE_ORDER = {
    Scope.THREAD: 0,
    Scope.SESSION: 1,
    Scope.GLOBAL: 2,
}


def _ordered_unique_scopes(scopes: list[Scope]) -> list[Scope]:
    return sorted(set(scopes), key=lambda item: _SCOPE_ORDER[item])


def _dedupe_clean_strings(values: list[str]) -> list[str]:
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


class TransportKind(str, Enum):
    STDIO = "stdio"


class TransportState(str, Enum):
    INACTIVE = "inactive"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILED = "failed"


class AuditOperation(str, Enum):
    REGISTER = "register"
    ACTIVATE = "activate"
    REFRESH = "refresh"
    DEACTIVATE = "deactivate"
    UNREGISTER = "unregister"
    RESTORE = "restore"
    CLEAR_STALE = "clear_stale"
    CALL = "call"
    HEALTH_CHECK = "health_check"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    FAILED = "failed"


class ErrorInfo(BaseModel):
    code: str
    message: str
    namespace: str | None = None
    retryable: bool = False
    details: dict[str, Any] | None = None


class ToolsetTransport(BaseModel):
    kind: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class ToolSchema(BaseModel):
    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    tool_hash: str


class ToolsetFutureCapabilities(BaseModel):
    supports_tasks: bool = False
    supports_triggers: bool = False
    supports_streaming: bool = False
    supports_reference_results: bool = False


class ToolsetGuidanceSource(BaseModel):
    kind: Literal["inline", "file", "registered"]
    title: str
    summary: str | None = None
    path: str | None = None
    content: str | None = None


class ToolsetGuidanceSourceSummary(BaseModel):
    kind: Literal["inline", "file", "registered"]
    title: str
    summary: str | None = None
    path: str | None = None


class ToolsetGuidanceDetail(BaseModel):
    kind: Literal["inline", "file", "registered"]
    title: str
    summary: str | None = None
    path: str | None = None
    content: str


class ToolsetCompositionExample(BaseModel):
    id: str
    title: str
    summary: str | None = None
    kind: Literal["batch", "program"]
    tags: list[str] = Field(default_factory=list)
    required_namespaces: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolsetExampleSummary(BaseModel):
    id: str
    title: str
    summary: str | None = None
    kind: Literal["batch", "program"]
    tags: list[str] = Field(default_factory=list)
    required_namespaces: list[str] = Field(default_factory=list)


class ToolsetExampleDetail(ToolsetExampleSummary):
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolsetCapabilityFlags(BaseModel):
    has_cached_contract: bool = False
    has_mounted_contract: bool = False
    has_guidance: bool = False
    has_recipes: bool = False
    has_examples: bool = False
    supports_health_check: bool = True
    supports_batch_composition: bool = True
    supports_program_composition: bool = True
    supports_structured_outputs: bool = False
    requires_auth: bool = False
    requires_workspace_root: bool = False
    has_future_task_metadata: bool = False
    has_future_trigger_metadata: bool = False
    has_future_streaming_metadata: bool = False
    has_future_reference_metadata: bool = False


class ToolsetQualitySummary(BaseModel):
    score: int
    grade: Literal["excellent", "good", "thin", "risky"]
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommended_next_action: Literal["use", "inspect", "refresh", "add_metadata", "avoid_until_fixed"]


class ToolsetQualityInspection(BaseModel):
    namespace: str
    title: str
    capability_flags: ToolsetCapabilityFlags
    quality: ToolsetQualitySummary


class ToolsetQualityInspectionResult(BaseModel):
    count: int
    quality: list[ToolsetQualityInspection] = Field(default_factory=list)
    missing: list[ErrorInfo] = Field(default_factory=list)


class CatalogPackToolset(BaseModel):
    namespace: str
    title: str
    description: str
    transport: ToolsetTransport
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    activation_hint: str | None = None
    cost_hint: str | None = None
    latency_hint: str | None = None
    trust_hint: str = "unknown"
    future_capabilities: ToolsetFutureCapabilities = Field(default_factory=ToolsetFutureCapabilities)
    guidance_sources: list[ToolsetGuidanceSource] = Field(default_factory=list)
    composition_examples: list[ToolsetCompositionExample] = Field(default_factory=list)
    default_scope: Scope = Scope.THREAD
    supported_scopes: list[Scope] = Field(default_factory=lambda: list(Scope))
    restorable_scopes: list[Scope] = Field(default_factory=list)
    restore_requires_identity: bool = True
    restore_requires_explicit_request: bool = True
    auth_required: bool = False
    required_env: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_metadata(self) -> CatalogPackToolset:
        self.tags = _dedupe_clean_strings(self.tags)
        self.aliases = _dedupe_clean_strings(self.aliases)
        self.examples = _dedupe_clean_strings(self.examples)
        self.recipes = _dedupe_clean_strings(self.recipes)
        self.required_env = _dedupe_clean_strings(self.required_env)
        self.category = self.category.strip().lower().replace(" ", "_") or "general"
        return self


class CatalogPack(BaseModel):
    version: int = 1
    name: str
    description: str | None = None
    toolsets: list[CatalogPackToolset] = Field(default_factory=list)


class ToolsetExampleListResult(BaseModel):
    namespace: str
    count: int
    examples: list[ToolsetExampleSummary] = Field(default_factory=list)


class ToolsetGuidanceLoadResult(BaseModel):
    namespace: str
    count: int
    guidance: list[ToolsetGuidanceDetail] = Field(default_factory=list)


class ToolsetRecord(BaseModel):
    namespace: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    category: str = "general"
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    activation_hint: str | None = None
    cost_hint: str | None = None
    latency_hint: str | None = None
    trust_hint: str = "unknown"
    future_capabilities: ToolsetFutureCapabilities = Field(default_factory=ToolsetFutureCapabilities)
    guidance_sources: list[ToolsetGuidanceSource] = Field(default_factory=list)
    composition_examples: list[ToolsetCompositionExample] = Field(default_factory=list)
    transport: ToolsetTransport
    default_scope: Scope = Scope.THREAD
    supported_scopes: list[Scope] = Field(default_factory=lambda: list(Scope))
    restorable_scopes: list[Scope] = Field(default_factory=list)
    recoverable_scopes: list[Scope] = Field(default_factory=list)
    restore_requires_identity: bool = True
    restore_requires_explicit_request: bool = True
    loaded_scopes: list[Scope] = Field(default_factory=list)
    transport_state: TransportState = TransportState.INACTIVE
    stale: bool = False
    stale_reason: str | None = None
    schema_hash: str | None = None
    previous_schema_hash: str | None = None
    tool_count: int = 0
    last_activated_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    last_known_good_at: datetime | None = None
    last_restored_at: datetime | None = None
    last_health_checked_at: datetime | None = None
    last_health_status: HealthStatus | None = None
    last_health_observed_schema_hash: str | None = None
    last_health_observed_tool_count: int | None = None
    last_health_error: ErrorInfo | None = None
    last_error: ErrorInfo | None = None
    auth_required: bool = False
    required_env: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope_policy(self) -> ToolsetRecord:
        self.supported_scopes = _ordered_unique_scopes(self.supported_scopes)
        self.restorable_scopes = _ordered_unique_scopes(self.restorable_scopes)
        self.recoverable_scopes = _ordered_unique_scopes(self.recoverable_scopes)
        self.loaded_scopes = _ordered_unique_scopes(self.loaded_scopes)
        self.category = self.category.strip().lower().replace(" ", "_") or "general"
        self.required_env = _dedupe_clean_strings(self.required_env)
        seen_example_ids: set[str] = set()
        for source in self.guidance_sources:
            source.title = source.title.strip()
            if not source.title:
                raise ValueError("guidance source title cannot be empty")
            source.summary = source.summary.strip() if source.summary is not None else None
            source.path = source.path.strip() if source.path is not None else None
            source.content = source.content.strip() if source.content is not None else None

        for example in self.composition_examples:
            example.id = example.id.strip()
            example.title = example.title.strip()
            if not example.id or not all(
                character.isalnum() or character in {"_", "-"} for character in example.id
            ):
                raise ValueError(
                    "composition example ids may only contain letters, numbers, underscores, and hyphens"
                )
            if example.id in seen_example_ids:
                raise ValueError(f"duplicate composition example id: {example.id}")
            seen_example_ids.add(example.id)
            if not example.title:
                raise ValueError("composition example title cannot be empty")
            example.summary = example.summary.strip() if example.summary is not None else None
            example.tags = _dedupe_clean_strings(example.tags)
            example.required_namespaces = _dedupe_clean_strings(example.required_namespaces)

        if self.default_scope not in self.supported_scopes:
            raise ValueError(
                f"default_scope '{self.default_scope.value}' must be included in supported_scopes"
            )

        unsupported_loaded = [scope.value for scope in self.loaded_scopes if scope not in self.supported_scopes]
        if unsupported_loaded:
            raise ValueError(
                "loaded_scopes must stay within supported_scopes"
                f" (unsupported: {', '.join(unsupported_loaded)})"
            )

        unsupported_restorable = [
            scope.value for scope in self.restorable_scopes if scope not in self.supported_scopes
        ]
        if unsupported_restorable:
            raise ValueError(
                "restorable_scopes must stay within supported_scopes"
                f" (unsupported: {', '.join(unsupported_restorable)})"
            )

        if Scope.THREAD in self.restorable_scopes:
            raise ValueError("thread scope cannot be marked restorable")

        unsupported_recoverable = [
            scope.value for scope in self.recoverable_scopes if scope not in self.supported_scopes
        ]
        if unsupported_recoverable:
            raise ValueError(
                "recoverable_scopes must stay within supported_scopes"
                f" (unsupported: {', '.join(unsupported_recoverable)})"
            )

        non_restorable_recoverable = [
            scope.value for scope in self.recoverable_scopes if scope not in self.restorable_scopes
        ]
        if non_restorable_recoverable:
            raise ValueError(
                "recoverable_scopes must stay within restorable_scopes"
                f" (non-restorable: {', '.join(non_restorable_recoverable)})"
            )

        return self


class SchemaSnapshot(BaseModel):
    namespace: str
    schema_hash: str
    tool_count: int
    tools: list[ToolSchema]
    captured_at: datetime


class StoredAuditEvent(BaseModel):
    sequence: int = 0
    timestamp: datetime
    operation: AuditOperation
    outcome: AuditOutcome
    namespace: str | None = None
    scope: Scope | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class AuditEvent(BaseModel):
    sequence: int
    timestamp: datetime
    operation: AuditOperation
    outcome: AuditOutcome
    namespace: str | None = None
    scope: Scope | None = None
    registration_present: bool | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: ErrorInfo | None = None


class AuditQuery(BaseModel):
    namespace: str | None = None
    operation: AuditOperation | None = None
    outcome: AuditOutcome | None = None
    registration_present: bool | None = None
    limit: int = 50


class AuditQueryResult(BaseModel):
    count: int
    events: list[AuditEvent] = Field(default_factory=list)


class SchemaShapeSummary(BaseModel):
    root_type: str | None = None
    property_names: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)
    property_count: int = 0
    item_type: str | None = None
    has_additional_properties: bool | None = None


class ContractAvailability(BaseModel):
    registered: bool = False
    cached: bool = False
    mounted: bool = False
    stale: bool = False
    missing: bool = False


class ToolContractSummary(BaseModel):
    name: str
    mounted_name: str | None = None
    title: str | None = None
    description: str | None = None
    tool_hash: str | None = None
    input_schema: SchemaShapeSummary = Field(default_factory=SchemaShapeSummary)
    output_schema: SchemaShapeSummary | None = None


class MountedToolDescription(BaseModel):
    namespace: str
    mounted_name: str
    source_name: str
    title: str | None = None
    description: str | None = None
    tool_hash: str | None = None
    input_schema: SchemaShapeSummary = Field(default_factory=SchemaShapeSummary)
    output_schema: SchemaShapeSummary | None = None
    required_inputs: list[str] = Field(default_factory=list)


class MountedToolDescriptionResult(BaseModel):
    count: int
    descriptions: list[MountedToolDescription] = Field(default_factory=list)
    missing: list[dict[str, Any]] = Field(default_factory=list)


class ToolsetContractInspection(BaseModel):
    namespace: str
    source: Literal["cached", "mounted"]
    availability: ContractAvailability
    transport_state: TransportState | None = None
    stale_reason: str | None = None
    schema_hash: str | None = None
    previous_schema_hash: str | None = None
    tool_count: int = 0
    captured_at: datetime | None = None
    last_known_good_at: datetime | None = None
    tools: list[ToolContractSummary] = Field(default_factory=list)


class ContractInspectionResult(BaseModel):
    count: int
    contracts: list[ToolsetContractInspection] = Field(default_factory=list)


class ToolContractDelta(BaseModel):
    change: Literal["added", "removed", "changed"]
    name: str
    mounted_name: str | None = None
    previous_summary: ToolContractSummary | None = None
    current_summary: ToolContractSummary | None = None


class ToolsetContractDiff(BaseModel):
    namespace: str
    source: Literal["cached", "mounted"]
    baseline: Literal["previous"] = "previous"
    availability: ContractAvailability
    previous_available: bool = False
    diff_available: bool = False
    transport_state: TransportState | None = None
    stale_reason: str | None = None
    current_schema_hash: str | None = None
    previous_schema_hash: str | None = None
    current_tool_count: int = 0
    previous_tool_count: int = 0
    current_captured_at: datetime | None = None
    previous_captured_at: datetime | None = None
    has_changes: bool = False
    added_tools: list[str] = Field(default_factory=list)
    removed_tools: list[str] = Field(default_factory=list)
    changed_tools: list[str] = Field(default_factory=list)
    changes: list[ToolContractDelta] = Field(default_factory=list)


class ContractDiffResult(BaseModel):
    count: int
    diffs: list[ToolsetContractDiff] = Field(default_factory=list)


class ProgramRuntimeBudget(BaseModel):
    max_program_length_chars: int
    max_ast_nodes: int
    max_loop_iterations: int
    max_tool_calls: int


class TransportRuntimeBudget(BaseModel):
    bootstrap_timeout_seconds: float
    mounted_tool_call_timeout_seconds: float
    health_check_timeout_seconds: float
    shutdown_timeout_seconds: float


class RuntimeBudgetInspection(BaseModel):
    mounted_tool_name_format: str = "namespace.tool"
    batch_reference_format: str = "step_id.path.to.value"
    program_return_variables_policy: Literal["requested_only"] = "requested_only"
    default_result_variable: str = "result"
    program: ProgramRuntimeBudget
    transport: TransportRuntimeBudget


class StateDocument(BaseModel):
    version: int = 2
    toolsets: list[ToolsetRecord] = Field(default_factory=list)
    schema_cache: list[SchemaSnapshot] = Field(default_factory=list)
    previous_schema_cache: list[SchemaSnapshot] = Field(default_factory=list)
    audit_log: list[StoredAuditEvent] = Field(default_factory=list)
    next_audit_sequence: int = 1


class SearchResult(BaseModel):
    namespace: str
    title: str
    description: str
    category: str
    tags: list[str]
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    activation_hint: str | None = None
    cost_hint: str | None = None
    latency_hint: str | None = None
    trust_hint: str = "unknown"
    transport: str
    loaded: bool
    stale: bool
    guidance_available: bool = False
    composition_example_count: int = 0
    capability_flags: ToolsetCapabilityFlags | None = None
    quality: ToolsetQualitySummary | None = None


class ToolsetSummary(BaseModel):
    namespace: str
    title: str
    description: str
    category: str
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    activation_hint: str | None = None
    cost_hint: str | None = None
    latency_hint: str | None = None
    trust_hint: str = "unknown"
    loaded: bool = False
    stale: bool = False
    transport_state: TransportState = TransportState.INACTIVE
    tool_count: int = 0
    auth_required: bool = False
    guidance_available: bool = False
    composition_example_count: int = 0
    future_capabilities: ToolsetFutureCapabilities = Field(default_factory=ToolsetFutureCapabilities)
    capability_flags: ToolsetCapabilityFlags | None = None
    quality: ToolsetQualitySummary | None = None


class ToolboxCategoryOverview(BaseModel):
    category: str
    count: int
    loaded_count: int = 0
    stale_count: int = 0
    examples: list[str] = Field(default_factory=list)
    toolsets: list[ToolsetSummary] = Field(default_factory=list)


class ToolboxOverview(BaseModel):
    purpose: str
    when_to_use: list[str] = Field(default_factory=list)
    discovery_tools: list[dict[str, str]] = Field(default_factory=list)
    category_count: int
    toolset_count: int
    categories: list[ToolboxCategoryOverview] = Field(default_factory=list)


class ToolsetSuggestion(BaseModel):
    namespace: str
    title: str
    description: str
    category: str
    score: int
    reasons: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    activation_hint: str | None = None
    cost_hint: str | None = None
    latency_hint: str | None = None
    trust_hint: str = "unknown"
    loaded: bool = False
    stale: bool = False
    transport_state: TransportState = TransportState.INACTIVE
    tool_count: int = 0
    auth_required: bool = False
    guidance_available: bool = False
    composition_example_count: int = 0
    capability_flags: ToolsetCapabilityFlags | None = None
    quality: ToolsetQualitySummary | None = None


class ToolsetSuggestionResult(BaseModel):
    task: str
    count: int
    suggestions: list[ToolsetSuggestion] = Field(default_factory=list)
    searched_fields: list[str] = Field(default_factory=list)


class ToolboxBrief(BaseModel):
    task: str | None = None
    purpose: str
    recommended_flow: list[dict[str, str]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    active_toolsets: list[ToolsetSummary] = Field(default_factory=list)
    suggestions: list[ToolsetSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolsetActivationPlanStep(BaseModel):
    action: str
    namespaces: list[str] = Field(default_factory=list)
    scope: Scope | None = None
    reason: str
    tool: str | None = None


class ToolsetActivationPlan(BaseModel):
    task: str
    scope: Scope
    selected_namespaces: list[str] = Field(default_factory=list)
    already_loaded: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    steps: list[ToolsetActivationPlanStep] = Field(default_factory=list)
    suggestions: list[ToolsetSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolsetGuide(BaseModel):
    toolset: ToolsetSummary
    when_to_use: list[str] = Field(default_factory=list)
    recipes: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    quality: ToolsetQualitySummary | None = None
    guidance_available: bool = False
    guidance_sources: list[ToolsetGuidanceSourceSummary] = Field(default_factory=list)
    composition_examples: list[ToolsetExampleSummary] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ToolboxCatalogIssue(BaseModel):
    namespace: str
    severity: Literal["info", "warning"]
    missing_fields: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ToolboxCatalogAudit(BaseModel):
    toolset_count: int
    issue_count: int
    issues: list[ToolboxCatalogIssue] = Field(default_factory=list)
    quality: list[ToolsetQualityInspection] = Field(default_factory=list)
    checked_fields: list[str] = Field(default_factory=list)


class ActivationResult(BaseModel):
    namespace: str
    scope: Scope
    tool_count: int
    schema_hash: str


class HealthCheckResult(BaseModel):
    namespace: str
    status: HealthStatus
    transport_state: TransportState
    loaded_scopes: list[Scope] = Field(default_factory=list)
    stale: bool = False
    stale_reason: str | None = None
    cached_schema_hash: str | None = None
    observed_schema_hash: str | None = None
    observed_tool_count: int | None = None
    last_checked_at: datetime | None = None
    error: ErrorInfo | None = None


class RestoreResult(BaseModel):
    namespace: str
    restored_scopes: list[Scope] = Field(default_factory=list)
    tool_count: int
    schema_hash: str
    reused_runtime: bool = False


class ClearStaleResult(BaseModel):
    namespace: str
    transport_state: TransportState
    cleared_stale_reason: str | None = None


class RefreshResult(BaseModel):
    namespace: str
    old_schema_hash: str | None = None
    new_schema_hash: str
    added_tools: list[str] = Field(default_factory=list)
    removed_tools: list[str] = Field(default_factory=list)
    changed_tools: list[str] = Field(default_factory=list)


class MountedTool(BaseModel):
    namespace: str
    mounted_name: str
    source_name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


class BatchStep(BaseModel):
    id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class BatchStepResult(BaseModel):
    id: str
    tool: str
    resolved_arguments: dict[str, Any] = Field(default_factory=dict)
    data: Any | None = None
    text: str | None = None
    is_error: bool = False
    error_message: str | None = None


class BatchRunResult(BaseModel):
    success: bool = True
    error: ErrorInfo | None = None
    step_count: int
    completed_steps: int
    stopped_on_error: bool = False
    final_step: str | None = None
    final_data: Any | None = None
    final_text: str | None = None
    results: list[BatchStepResult] = Field(default_factory=list)


class ProgramCallTrace(BaseModel):
    index: int
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    data: Any | None = None
    text: str | None = None
    is_error: bool = False
    error_message: str | None = None


class ProgramRunResult(BaseModel):
    success: bool = True
    error: ErrorInfo | None = None
    result_variable: str = "result"
    final_data: Any | None = None
    final_text: str | None = None
    call_count: int = 0
    calls: list[ProgramCallTrace] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    contract_summaries: list[MountedToolDescription] = Field(default_factory=list)
    missing_contract_summaries: list[dict[str, Any]] = Field(default_factory=list)

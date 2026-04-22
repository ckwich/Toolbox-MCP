# Phase 1: Audit and Observability - Context

**Gathered:** 2026-04-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Add durable lifecycle event history and host-visible recent failure summaries to Toolbox. This phase is about observability of the existing control plane, not about adding new transport types, UI work, or broader product surface.

</domain>

<decisions>
## Implementation Decisions

### Audit storage
- **D-01:** Start with JSON-backed local persistence, consistent with the existing registry/state model.
- **D-02:** Keep audit history bounded and queryable rather than introducing an external database or log sink.
- **D-03:** Unregistering a toolset removes registration and schema state, but recent audit history remains available until retention eviction.

### Event model
- **D-04:** Every lifecycle event should include timestamp, operation, outcome, and namespace where applicable.
- **D-05:** Failure events should capture structured error code and enough details for host recovery decisions.
- **D-06:** Event records should indicate whether the namespace is still currently registered so hosts can understand post-unregistration history.

### Surface area
- **D-07:** Observability should stay inside the control plane through compact query/status tools, not by mounting more downstream tools.
- **D-08:** `get_toolset_status` should surface recent failure context directly so hosts do not need to scan the raw event stream for common cases.

### the agent's Discretion
- Retention count and exact event ordering strategy
- Whether audit history lives inside `state.json` or a closely related adjacent file, as long as the host-facing behavior stays simple and local-first
- The exact shape of status summary fields beyond the required recent failure context

</decisions>

<specifics>
## Specific Ideas

- Keep the query surface compact: recent events, filters, and status summary are enough for this phase.
- Use the same structured error vocabulary already present in the control plane where possible.
- Do not treat audit logging as a telemetry platform yet; this phase is about trustworthy local observability.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Product and semantics
- `spec.med` — Control-plane intent, event/audit log recommendation, error model, safety requirements, and lifecycle semantics
- `README.md` — Current implemented scope and public framing of the product
- `.planning/PROJECT.md` — Locked decisions on restore semantics and audit retention
- `.planning/ROADMAP.md` — Phase ordering, especially the decision to do scope semantics before recovery automation

### Existing implementation
- `toolbox/service.py` — Lifecycle operations, mounted-tool composition, and the core place where audit events will need to be emitted
- `toolbox/transport_manager.py` — Runtime lifecycle, timeout behavior, and failure transitions that should be auditable
- `toolbox/registry.py` — JSON persistence layer that will likely absorb or coordinate audit storage
- `toolbox/models.py` — Existing structured models and error payloads that the audit model should align with

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `JsonStateStore`: existing JSON persistence with locking and atomic replace semantics
- `ErrorInfo`: existing structured error contract that audit entries should reuse instead of inventing new error shapes
- `ToolboxService._error(...)`: consistent control-plane error serialization helper

### Established Patterns
- Lifecycle methods already centralize behavior in `ToolboxService`, so event emission should happen there rather than inside the MCP tool wrappers.
- Structured result models are already returned from batch/program flows; audit query results should follow the same compact, typed style.
- Tests currently live in `tests/test_service.py` and cover both service-level and full server behavior, which is the right place for audit regressions too.

### Integration Points
- Registration, activation, refresh, deactivation, unregistration, and mounted-tool failure handling are the main event sources for this phase.
- `get_toolset_status` is the right place to surface recent-failure summary data after audit history exists.
- Unregistration is explicitly in scope for audit retention behavior even though it removes the live registration itself.

</code_context>

<deferred>
## Deferred Ideas

- External telemetry sinks or streaming log export
- Dashboard/UI rendering of lifecycle history
- Per-session or remote-host audit partitioning beyond current workspace-local behavior

</deferred>

---

*Phase: 01-audit-observability*
*Context gathered: 2026-04-21*

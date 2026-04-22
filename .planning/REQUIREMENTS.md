# Requirements: Toolbox MCP

**Defined:** 2026-04-21
**Core Value:** Hosts can keep their always-on MCP surface tiny while still invoking the right toolsets on demand in the same thread.

## v1 Requirements

### Observability

- [x] **OBS-01**: Host can list recent Toolbox lifecycle events with timestamp, namespace, operation, outcome, and registration presence
- [x] **OBS-02**: Registration, activation, refresh, deactivation, unregistration, timeout, and transport failure paths append audit events automatically
- [x] **OBS-03**: Host can filter lifecycle events by namespace, event type, success/failure, and registration presence
- [x] **OBS-04**: Toolset status surfaces recent failure summary without requiring the host to scan raw logs
- [x] **OBS-05**: Unregistering a toolset does not silently erase its recent audit history; retained events age out through bounded retention

### Introspection

- [x] **INSP-01**: Host can inspect cached tool contracts for a toolset without activating it
- [x] **INSP-02**: Host can inspect mounted tool contracts for active toolsets with input/output schema summaries
- [x] **INSP-03**: Host can request an on-demand diff between current and previous schema snapshots for a toolset
- [x] **INSP-04**: Introspection output identifies whether contract data is cached, mounted, stale, or missing

### Scope

- [x] **SCOPE-01**: Toolbox exposes loaded scopes per toolset and never widens scope implicitly during lifecycle or recovery actions
- [x] **SCOPE-02**: `thread`-scoped toolsets are never auto-restored on process startup without explicit host rehydration
- [x] **SCOPE-03**: `session` and `global` restoration only occurs when stable host identity and explicit restore configuration are present
- [x] **SCOPE-04**: Scope isolation across `thread`, `session`, and `global` is covered by regression tests and visible in status/reporting

Current phase note:
- `03-01` delivered explicit scope-policy metadata, supported-scope enforcement for activation, and validation that `thread` cannot be marked restorable
- `03-02` completed cross-scope lifecycle isolation, blocked implicit refresh activation for inactive namespaces, and added startup reconciliation that clears loaded scopes without explicit restore context

### Health

- [x] **HLTH-01**: Toolbox can perform optional health checks against active runtimes without hanging the host
- [x] **HLTH-02**: Failed health checks mark toolsets stale or failed with structured reasons and audit events
- [x] **HLTH-03**: Toolbox can restore previously active restorable toolsets on startup when configured to do so
- [x] **HLTH-04**: Host can clear or reconcile stale state through the control plane after recovery

Current phase note:
- `04-01` delivered bounded active-runtime health checks, host-visible health status blocks, and stale/failed transitions with audit events
- `04-02` added persisted recoverable-scope tracking, explicit restore tooling, optional startup restore, and host-visible stale reconciliation flows

### Composition

- [x] **COMP-01**: Host or model can describe mounted tools cheaply before writing a batch or program
- [x] **COMP-02**: Program callers can request specific result variables and contract summaries without leaking full initial context
- [x] **COMP-03**: Progressive discovery and programmatic tool-calling flows are documented with host-facing examples
- [x] **COMP-04**: Runtime budget settings are inspectable so hosts can tune program constraints intentionally

Current phase note:
- `05-01` delivered `describe_mounted_tools` for cheap live per-tool summaries and `inspect_runtime_budgets` for explicit program/transport limit inspection
- `05-02` delivered `return_contract_summaries` in `run_tool_program` plus a host-facing composition workflow guide and examples

## v2 Requirements

### Transports

- **TRNS-01**: Toolbox supports non-stdio transports behind the same registration and activation model
- **TRNS-02**: Toolbox can represent connector-backed toolsets alongside local subprocess toolsets

### Auth and Apps

- **AUTH-01**: Toolbox surfaces downstream auth requirements and re-auth needs explicitly
- **APP-01**: Toolbox can ship host guidance or skills-like usage metadata alongside managed toolsets

## Out of Scope

| Feature | Reason |
|---------|--------|
| Domain-specific business tools | Toolbox should stay a reusable control plane rather than absorbing downstream product logic |
| Unrestricted code execution runtime | The current product goal is safe composition, not a general sandbox |
| Multi-tenant storage or fleet-grade operations | Current scope is local/workspace-level product maturity, not infrastructure scale-out |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| OBS-01 | Phase 1 | Completed |
| OBS-02 | Phase 1 | Completed |
| OBS-03 | Phase 1 | Completed |
| OBS-04 | Phase 1 | Completed |
| OBS-05 | Phase 1 | Completed |
| INSP-01 | Phase 2 | Completed |
| INSP-02 | Phase 2 | Completed |
| INSP-03 | Phase 2 | Completed |
| INSP-04 | Phase 2 | Completed |
| SCOPE-01 | Phase 3 | Completed |
| SCOPE-02 | Phase 3 | Completed |
| SCOPE-03 | Phase 3 | Completed |
| SCOPE-04 | Phase 3 | Completed |
| HLTH-01 | Phase 4 | Completed |
| HLTH-02 | Phase 4 | Completed |
| HLTH-03 | Phase 4 | Completed |
| HLTH-04 | Phase 4 | Completed |
| COMP-01 | Phase 5 | Completed |
| COMP-02 | Phase 5 | Completed |
| COMP-03 | Phase 5 | Completed |
| COMP-04 | Phase 5 | Completed |

**Coverage:**
- v1 requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0

---
*Requirements defined: 2026-04-21*
*Last updated: 2026-04-22 after completing 05-02 host-facing composition docs, examples, and helper affordances*

# Roadmap: Toolbox MCP

## Overview

The main v1 roadmap is complete. This follow-on phase captures the minimum polish needed
before broader real-world use: durable CI, a clear first-run path, explicit
state-compatibility notes, and a current operational handoff.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions if needed later

- [x] **Phase 1: Audit and Observability** - Persist lifecycle history and expose recent failures clearly to hosts
- [x] **Phase 2: Contract Introspection and Diff** - Make cached and mounted schemas inspectable without broad tool exposure
- [x] **Phase 3: Scope Semantics and Restore Policy** - Formalize and enforce `thread`/`session`/`global` behavior before recovery automation
- [x] **Phase 4: Health Monitoring and Recovery** - Add optional health checks, restore flows, and stale-state reconciliation
- [x] **Phase 5: Composition UX and Runtime Tuning** - Improve discovery and programmatic composition ergonomics on top of the hardened harness
- [x] **Phase 6: Release and Operability Polish** - Add CI, first-run guidance, compatibility notes, and a current operational handoff
- [x] **Phase 7: Agent-Facing Discovery** - Make deferred capabilities self-describing enough for agents to know when to ask Toolbox

## Phase Details

### Phase 1: Audit and Observability
**Goal**: Add durable lifecycle audit history and host-visible recent failure summaries.
**Depends on**: Nothing
**Requirements**: OBS-01, OBS-02, OBS-03, OBS-04, OBS-05
**Success Criteria** (what must be TRUE):
1. Host can query recent lifecycle event history with filters by namespace and outcome.
2. Registration, activation, refresh, deactivation, unregistration, timeout, and transport failure paths emit structured audit events.
3. `get_toolset_status` exposes recent failure context without requiring raw log scanning.
4. Unregistering a toolset does not silently erase its recent auditable history.
**Plans**: 2 plans

Plans:
- [x] 01-01: Add audit event model, persistence, and lifecycle emission hooks
- [x] 01-02: Expose audit query tooling and enrich status summaries

### Phase 2: Contract Introspection and Diff
**Goal**: Give hosts and models a cheap way to inspect cached and mounted contracts deliberately.
**Depends on**: Phase 1
**Requirements**: INSP-01, INSP-02, INSP-03, INSP-04
**Success Criteria** (what must be TRUE):
1. Host can inspect cached tool contracts without activating a toolset.
2. Host can inspect mounted tool contracts for active toolsets with clear schema summaries.
3. On-demand schema diffing reports current versus previous contract changes in a compact format.
**Plans**: 2 plans

Plans:
- [x] 02-01: Add cached and mounted contract inspection surfaces
- [x] 02-02: Add on-demand schema diff tooling and regression coverage

### Phase 3: Scope Semantics and Restore Policy
**Goal**: Make scope behavior explicit, enforced, and testable before layering recovery automation on top.
**Depends on**: Phase 2
**Requirements**: SCOPE-01, SCOPE-02, SCOPE-03, SCOPE-04
**Success Criteria** (what must be TRUE):
1. Toolbox exposes and enforces clear behavior for `thread`, `session`, and `global` activation state.
2. Toolbox never widens scope implicitly during restore or reconciliation actions.
3. `thread`-scoped toolsets are not auto-restored on startup unless the host explicitly rehydrates that scope.
**Plans**: 2 plans

Plans:
- [x] 03-01: Add explicit scope metadata, restorable-scope rules, and restore-policy documentation
- [x] 03-02: Add scope isolation enforcement and regression coverage across lifecycle flows

### Phase 4: Health Monitoring and Recovery
**Goal**: Add explicit health-check and recovery flows instead of relying only on passive failure handling.
**Depends on**: Phase 3
**Requirements**: HLTH-01, HLTH-02, HLTH-03, HLTH-04
**Success Criteria** (what must be TRUE):
1. Active runtimes can be health-checked without leaving the host hanging.
2. Health-check failures mark toolsets stale or failed with structured reasons and audit events.
3. Only restorable scopes are recovered on startup or reconciliation flows, consistent with the scope phase rules.
4. Hosts can explicitly clear stale or failed runtime state without reconnecting a toolset.
**Plans**: 2 plans

Plans:
- [x] 04-01: Add active-runtime health checks and stale transition handling
- [x] 04-02: Add restore-on-startup and stale reconciliation flows for restorable scopes

### Phase 5: Composition UX and Runtime Tuning
**Goal**: Improve the ergonomics of progressive discovery and programmatic tool calling without widening the always-on surface.
**Depends on**: Phase 4
**Requirements**: COMP-01, COMP-02, COMP-03, COMP-04
**Success Criteria** (what must be TRUE):
1. Hosts and models can cheaply describe mounted tools and runtime budgets before composing.
2. Program callers can request only the context and variables they actually need.
3. The repo includes concrete discovery and composition examples that reflect the intended host usage model.
**Plans**: 2 plans

Plans:
- [x] 05-01: Add mounted-tool description and runtime-budget inspection surfaces
- [x] 05-02: Add host-facing composition docs, examples, and helper affordances

### Phase 6: Release and Operability Polish
**Goal**: Make the completed v1 control plane easier to verify, try, and hand off safely.
**Depends on**: Phase 5
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04
**Success Criteria** (what must be TRUE):
1. The repo has automated verification on Windows and Linux, including the strict Windows warning lane.
2. A new developer or operator can follow a short quickstart to exercise the seeded fake toolset flow.
3. State-version and transport-secret persistence behavior are documented clearly enough for safe local use and migration.
4. `HANDOFF.md` describes the current repo and operating model, not the original bootstrap plan.
**Plans**: 1 plan

Plans:
- [x] 06-01: Add CI, first-run guidance, compatibility notes, and an operational handoff

### Phase 7: Agent-Facing Discovery
**Goal**: Give agents a tiny orientation layer for deferred capabilities without widening the default tool surface.
**Depends on**: Phase 6
**Requirements**: AGENT-01, AGENT-02, AGENT-03, AGENT-04
**Success Criteria** (what must be TRUE):
1. Agents can ask one cheap tool for the categories and examples of deferred capabilities currently registered in Toolbox.
2. Toolset registrations can carry agent-facing hints such as category, aliases, examples, activation guidance, cost, latency, and trust.
3. Search and suggestions use those hints so broad task intent can find a relevant hidden toolset.
4. The default Toolbox surface stays small and does not expose downstream raw schemas just to teach the agent what exists.
**Plans**: 1 plan

Plans:
- [x] 07-01: Add overview, rich metadata, and task-based toolset suggestions

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Audit and Observability | 2/2 | Completed | 01-01, 01-02 |
| 2. Contract Introspection and Diff | 2/2 | Completed | 02-01, 02-02 |
| 3. Scope Semantics and Restore Policy | 2/2 | Completed | 03-01, 03-02 |
| 4. Health Monitoring and Recovery | 2/2 | Completed | 04-01, 04-02 |
| 5. Composition UX and Runtime Tuning | 2/2 | Completed | 05-01, 05-02 |
| 6. Release and Operability Polish | 1/1 | Completed | 06-01 |
| 7. Agent-Facing Discovery | 1/1 | Completed | 07-01 |

# Project State

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-04-22)

**Core value:** Hosts can keep their always-on MCP surface tiny while still invoking the right toolsets on demand in the same thread.
**Current focus:** Phase 8 agent guidance affordances completed - repo is ready for live Toolbox use and follow-on testing

## Current Position

Phase: 8 of 8 (Agent Guidance Affordances)
Plan: 1 of 1 in current phase
Status: Completed
Last activity: 2026-04-27 - Completed 08-01 brief, activation planning, guides, recipes, and catalog audit

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 13
- Average duration: -
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Audit and Observability | 2 | - | - |
| 2. Contract Introspection and Diff | 2 | - | - |
| 3. Scope Semantics and Restore Policy | 2 | - | - |
| 4. Health Monitoring and Recovery | 2 | - | - |
| 5. Composition UX and Runtime Tuning | 2 | - | - |
| 6. Release and Operability Polish | 1 | - | - |
| 7. Agent-Facing Discovery | 1/1 | - | - |
| 8. Agent Guidance Affordances | 1/1 | - | - |

**Recent Trend:**
- Last 5 plans: 05-01, 05-02, 06-01, 07-01, 08-01
- Trend: Stable

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Planning should now be deliberate and artifact-backed instead of chat-only.
- The next milestone should prioritize production-readiness of the control plane over broad feature expansion.
- Scope semantics and restore policy must be specified before recovery automation is implemented.
- Hosts now have an explicit control-plane observability surface, so future phases can build on auditable behavior rather than inferred state.
- Contract inspection should stay summary-first; raw schema dumps would cut against the token-efficiency model.
- Contract diffing should reuse stored previous snapshots and return only compact change summaries, not raw before/after schemas.
- Scope activation is now reference-like across `thread`/`session`/`global`: adding a scope reuses the live runtime and removing one scope does not disconnect the namespace until the last scope is gone.
- Health checks are now explicit control-plane probes with bounded waits and host-visible summaries, not passive side effects hidden inside tool invocation paths.
- Recovery now preserves `recoverable_scopes` across startup, restores only policy-eligible scopes, and exposes stale reconciliation as an explicit control-plane action.
- Composition prep now has a cheap live tool-description surface and an explicit runtime-budget inspection surface, separate from heavier toolset-level contract inspection.
- Program results can now return only explicitly requested variables and mounted-tool summaries, and the repo includes a concrete host-facing composition workflow guide.
- Agent-facing discovery should add orientation and intent hints without exposing every downstream schema by default.
- Agent guidance should remain passive: brief, plan, guide, and audit helpers advise the agent but never auto-activate or dump downstream schemas.

### Pending Todos

None currently. Future work should start from a new planned milestone or a deferred item.

### Blockers/Concerns

- The main roadmap and agent guidance follow-on are complete; future work should start from a new milestone instead of reopening ad hoc iteration.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Transport | Remote transport support beyond stdio | Deferred | 2026-04-21 |
| Product | Unrestricted execution runtime | Deferred | 2026-04-21 |

## Session Continuity

Last session: 2026-04-22 00:00
Stopped at: Completed Phase 6 release/operability polish after the full v1 roadmap
Resume file: None

# Toolbox MCP

## What This Is

Toolbox is a control-plane MCP server for progressive discovery and on-demand tool invocation.
It keeps the always-loaded tool surface small, manages downstream MCP toolsets lazily, and gives hosts a safe way to activate, refresh, inspect, compose, and unload toolsets without starting a new conversation.

## Core Value

Hosts can keep their always-on MCP surface tiny while still invoking the right toolsets on demand in the same thread.

## Requirements

### Validated

- ✓ Metadata-first discovery and detailed toolset status work against a JSON-backed registry — existing
- ✓ Managed stdio toolsets can be registered, activated, refreshed, deactivated, and unregistered safely — existing
- ✓ Registered toolsets now expose explicit default, supported, and restorable scope policy metadata, and unsupported activations fail fast — Phase 3 / 03-01
- ✓ Scope lifecycles are now isolated across `thread`, `session`, and `global`: added scopes reuse the live runtime, inactive namespaces cannot refresh into mounted state, and startup clears loaded scopes without restore context — Phase 3 / 03-02
- ✓ Active runtimes can now be health-checked through the control plane, and failing probes produce stale/failed transitions plus audit events — Phase 4 / 04-01
- ✓ Recovery is now explicit and policy-aware: Toolbox preserves recoverable scopes across startup, can restore them on request or configured startup, and can clear stale state without reconnecting toolsets — Phase 4 / 04-02
- ✓ Activated toolsets mount real downstream tools under stable names and preserve last-known-good contracts on failed refresh — existing
- ✓ Hosts can inspect cached and mounted contracts, plus compact current-vs-previous diffs, without widening the default surface — existing
- ✓ Lifecycle audit history and recent failure summaries are queryable through the control plane — existing
- ✓ Hosts and models can now describe mounted tools cheaply and inspect runtime budgets before composing — Phase 5 / 05-01
- ✓ Hosts now have concrete composition workflows, examples, and narrow program return affordances for selected variables and contract summaries — Phase 5 / 05-02
- ✓ The repo now has cross-platform CI coverage for the verified runtime surface, including the strict Windows unraisable-warning lane — Phase 6 / 06-01
- ✓ Top-level docs now include a first-run quickstart, state-compatibility notes, and a current operational handoff — Phase 6 / 06-01
- ✓ Agents can now ask for a compact Toolbox overview and task-ranked toolset suggestions backed by richer registration metadata — Phase 7 / 07-01
- ✓ Agents can now ask for a low-token brief, dry-run activation plan, selected toolset guide, and catalog audit without auto-activating hidden tools — Phase 8 / 08-01
- ✓ Toolbox supports single-request batch composition and constrained scripted composition over mounted tools — existing
- ✓ Harness behavior is bounded and structured: lifecycle serialization, runtime timeouts, and stable failure envelopes are already in place — existing

### Active

- Phase 9 is planned as the next milestone: derived capability flags, quality summaries,
  lazy guidance-source indexing, composition examples, and inert future protocol metadata.

### Out of Scope

- Remote transports and connector-backed registrations beyond stdio — defer until the current control plane is more mature
- Domain-specific tool implementations — Toolbox stays a supervisor and broker, not a domain server
- Unrestricted code execution in program mode — the constrained runtime is intentional and should remain bounded

## Context

- The project started from the idea that MCP clients should use progressive discovery and programmatic tool calling instead of eagerly surfacing every schema all the time.
- `spec.med` is the current product specification and remains the main source of truth for supervisor behavior, error handling, activation semantics, and refresh guarantees.
- The current repo already proves the end-to-end core loop: registration, activation, refresh, deactivation, live tool brokering, batch composition, scripted composition, and recent harness hardening.
- The next work after v1 should prioritize production-readiness and operator clarity before widening the product surface indiscriminately.
- The next planned session should make registered toolsets easier for agents to rank and trust without activating them or loading raw downstream schemas.

## Constraints

- **Tech stack**: Python + FastMCP + Pydantic + JSON persistence — current implementation is already built here and should stay coherent
- **Protocol posture**: Tiny always-on control surface — token savings are part of the product, not an optimization after the fact
- **Safety**: Activation and refresh must stay atomic, and failed refresh must preserve last-known-good contracts — core trust requirement
- **Runtime discipline**: Structured errors, bounded waits, and explicit state transitions are required — hosts should not hang on bad downstream runtimes
- **Scope model**: `thread`, `session`, and `global` scopes remain first-class even if current test coverage is strongest around `thread`
- **Restore semantics**: `thread` scope is host-local and not auto-restored by default; only explicitly restorable scopes may be reactivated on startup
- **Audit retention**: Unregistering a toolset must not silently erase its recent lifecycle history; audit data ages out through bounded retention instead

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Toolbox is an always-loaded supervisor MCP rather than a one-shot host helper | Keeps discovery and mounting logic in one place and avoids pushing dynamic tool orchestration back into each host | ✓ Good |
| Downstream tools mount lazily under `namespace.tool` | Preserves a tiny control plane while still making active tools directly invokable | ✓ Good |
| Programmatic tool calling uses a constrained runtime, not arbitrary execution | The value is composition, not unbounded scripting risk | ✓ Good |
| Hardening came before broader feature expansion | Reliability at the broker/runtime boundary is a prerequisite for everything else | ✓ Good |
| Registration management belongs in the Toolbox control plane | Hosts should not need to mutate JSON state directly just to add or remove managed toolsets | ✓ Good |
| `thread`-scoped toolsets are not auto-restored on startup | Thread state belongs to the host/conversation boundary and should not be widened by Toolbox | ✓ Good |
| Scope policy is explicit registration metadata, not an inferred side effect of current activation state | Recovery and host reporting need a durable contract for supported and restorable scopes before deeper isolation logic lands | ✓ Good |
| Scope attachment is reference-like while a namespace stays live | Adding a second scope should not churn the runtime or schema snapshot for already active scopes | ✓ Good |
| Refresh must not implicitly activate an inactive namespace | Connecting a stale-but-inactive toolset would violate scope boundaries by mounting tools without an active scope | ✓ Good |
| Health checks should be explicit control-plane actions with bounded waits | Hosts need deliberate liveness probes that do not depend on downstream domain tool calls or hang the thread | ✓ Good |
| Audit history survives unregistration until retention eviction | Hosts need recent lifecycle history for debugging and trust, even after a toolset is removed | ✓ Good |
| Scope semantics deserve their own roadmap phase before recovery automation | Recovery behavior depends on clear scope contracts, not the other way around | ✓ Good |
| Recovery should persist `recoverable_scopes`, not raw mounted state | Startup reconciliation needs a durable restore intent without silently remounting toolsets | ✓ Good |
| Composition discovery should expose a tool-oriented live summary, not only toolset-oriented contract inspection | Pre-composition callers need the cheapest possible live view keyed by mounted tool name, not a heavier per-toolset inspection payload | ✓ Good |
| Program results should be able to echo only explicitly requested helper metadata | Hosts often need a narrow post-run contract view without reloading the full mounted inventory or leaking the whole initial context | ✓ Good |
| Post-v1 polish should improve verification and operator clarity instead of adding more product surface | The runtime and control-plane behavior are already feature-complete for the current milestone; the bigger risk is drift or onboarding friction | ✓ Good |
| CI must keep both the general lane and the strict Windows unraisable-warning lane | The repo has platform-sensitive subprocess behavior, so “passes on one machine” is not strong enough release evidence | ✓ Good |
| Toolbox needs an agent orientation layer in addition to raw search | Hidden capabilities are only useful if the agent can cheaply learn that categories such as skills, docs, or security scanning exist | ✓ Good |
| Agent guidance should be passive and plan-first | The agent should get enough help to choose and mount the right toolset, but Toolbox should not widen the visible surface or activate tools on its behalf | ✓ Good |
| Toolset quality should be derived, compact, and explainable | Agents need to know which registered toolsets are composable, guidance-backed, healthy, and safe to prefer without loading full schemas | Planned |
| Future MCP roadmap concepts should remain inert metadata until verified | Tasks, triggers, streaming, and reference results are useful concepts, but implementing behavior before client and SDK support is stable would make Toolbox brittle | Planned |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? Move them to Out of Scope with a reason.
2. Requirements validated? Move them to Validated with a phase reference.
3. New requirements emerged? Add them to Active.
4. Decisions to log? Add them to Key Decisions.
5. "What This Is" still accurate? Update it if reality drifted.

**After each milestone:**
1. Re-check the Core Value.
2. Review all Active and Out of Scope items.
3. Refresh Context with new product and codebase reality.

---
*Last updated: 2026-04-27 after planning Phase 9 toolset quality intelligence*

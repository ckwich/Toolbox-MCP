# Toolbox MCP

Toolbox is a small control-plane MCP server that manages the lifecycle of other MCP
toolsets. It keeps its own always-loaded surface small and lets hosts search, activate,
refresh, and deactivate managed toolsets without exposing every downstream schema all
at once.

## Current Slice

This first implementation focuses on a narrow end-to-end loop:

- JSON-backed registry and schema cache
- metadata-first discovery tools
- control-plane registration management via `register_toolset` and `unregister_toolsets`
- agent-facing discovery via `toolbox_brief`, `toolbox_overview`, `suggest_toolsets_for_task`,
  `plan_toolset_activation`, `get_toolset_guide`, and `audit_toolbox_catalog`
- scoped activation, live mounting, refresh, and deactivation
- bounded lifecycle audit logging with control-plane querying
- cached and mounted contract inspection with compact schema summaries
- cached and mounted contract diffing against the previous stored snapshot
- explicit scope policy metadata for `thread`, `session`, and `global`
- bounded active-runtime health checks with stale/failed transitions
- explicit restore and stale-reconciliation control-plane tools
- composition-oriented mounted-tool description tooling
- runtime-budget inspection for program and transport limits
- single-call mounted-tool composition via `run_tool_batch`
- constrained scripted composition via `run_tool_program`
- per-namespace lifecycle serialization for activate/refresh/deactivate
- bounded downstream runtime bootstrap, call, and shutdown timeouts
- structured failure envelopes for batch and program execution
- schema hashing and diff reporting
- read-only Codex skill discovery through a separate skill-loader MCP server
- one fake managed stdio MCP server for integration testing

When a toolset is activated, Toolbox mounts its downstream tools into the active tool
surface under stable names like `fake_stdio.fake_status`. When deactivated, those tools
are removed again.

Toolbox can now register additional managed stdio toolsets through its own MCP surface,
so hosts do not need to pre-seed every registration directly in the state file.

`toolbox_overview` is the tiny agent-facing map for deferred capabilities. It groups
registered toolsets by category and returns compact examples, hints, and toolset
summaries without activating anything or loading downstream schemas.

`toolbox_brief` is the cheapest first question for an agent to ask. It returns the
recommended Toolbox flow, currently active toolsets, task-specific suggestions when a
task is provided, and next-action hints without mounting anything.

`suggest_toolsets_for_task` turns a task description into a short ranked list of
registered toolsets. It searches namespace, title, description, category, tags, aliases,
examples, recipes, and activation hints, which lets an agent find a hidden skill loader
or scanner by intent instead of by exact toolset name.

`plan_toolset_activation` turns that shortlist into a dry-run activation plan. It tells
the agent which namespaces would be newly mounted for the requested scope, which are
already loaded, which should be skipped, and which follow-up inspection tool to call
before invoking downstream capabilities.

`get_toolset_guide` lazily loads compact per-toolset usage guidance after a candidate has
been selected. It returns recipes, examples, when-to-use hints, and safe next actions
without dumping raw downstream schemas.

`audit_toolbox_catalog` checks registered toolsets for thin metadata that would make them
hard for agents to discover or use. It is intended for improving the catalog itself, not
for normal task execution.

Toolset registrations now support agent-facing metadata fields:

- `category`: coarse capability bucket such as `skills`, `docs`, `security`, or `automation`
- `aliases`: alternate names or phrases an agent might use
- `examples`: short tasks that should make the agent consider the toolset
- `recipes`: compact workflow hints for using the toolset correctly after selection
- `activation_hint`: when to activate the toolset
- `cost_hint`, `latency_hint`, and `trust_hint`: compact planning signals for choosing among candidates

`run_tool_batch` provides a first step toward programmatic tool calling. It lets one
Toolbox call execute multiple mounted tool calls and pass data from earlier steps into
later ones using explicit references like `{"$from": "status.response.version"}`.
Batch runs now always return a structured result envelope, including top-level error
metadata when validation or a step fails.

`run_tool_program` is the next step up: a constrained Python-like runtime for mounted
tools. It supports local variables, dict/list literals, attribute/subscript access into
structured outputs, simple conditionals, bounded `for` loops, `+=`-style accumulation,
small collection helpers like `sum`, `sorted`, `range`, and `items`, and inline
`call_tool("namespace.tool", ...)` calls, all inside one Toolbox request. Program runs
return structured success or failure payloads with partial call traces, and local
variables are only returned when explicitly requested. Requested variables and final
program results are projected into JSON-safe values before they leave the control plane,
so opaque host objects do not break serialization.

Program call traces and batch step argument snapshots now redact argument values while
preserving the argument shape, so host-provided secrets do not echo back through
composition telemetry.

Program callers can also request specific mounted-tool contract summaries back through
`return_contract_summaries`, which keeps follow-up host steps narrow without re-querying
the entire mounted inventory.

Toolbox now also keeps a bounded local audit history for lifecycle and runtime failure
events. Hosts can query that history through `list_audit_events` using compact filters
like namespace, operation, outcome, and current registration presence. This is intended
for recent operational insight, not as a general telemetry backend.

`get_toolset_status` now includes a `recent_failure` summary when a namespace has recent
auditable failures. Hosts should use that summary for fast UI and retry decisions, and
only fall back to `list_audit_events` when they need the fuller event trail.

Toolbox also exposes deliberate contract inspection surfaces: `inspect_cached_contracts`
for last-known-good cached inventories and `inspect_mounted_contracts` for live active
inventories. These return compact schema summaries and availability flags like `cached`,
`mounted`, `stale`, and `missing` so hosts can inspect the contract state without loading
full raw schemas into the default control surface.

Toolbox now also exposes deliberate contract diff surfaces: `diff_cached_contracts` and
`diff_mounted_contracts`. These compare the current cached or mounted contract against the
previous stored snapshot for the same namespace and return compact change sets for added,
removed, and changed tools instead of dumping full raw schemas by default.

For composition prep specifically, Toolbox now exposes `describe_mounted_tools` and
`inspect_runtime_budgets`. `describe_mounted_tools` is the cheapest live tool-description
surface: it returns per-mounted-tool summaries keyed by `namespace.tool`, plus missing
reasons for inactive or unknown filters. `inspect_runtime_budgets` exposes the current
program limits and transport timeout settings so hosts can shape batches and scripted
programs intentionally before they submit them.

For complete host-facing examples, see [docs/composition-workflows.md](docs/composition-workflows.md).
For the optional Codex skill-loader MCP, see [docs/skill-loader-mcp.md](docs/skill-loader-mcp.md).

## Scope

Each registered toolset now carries explicit scope policy metadata:

- `default_scope`: the host-facing default activation scope for the toolset
- `supported_scopes`: the scopes Toolbox will accept for explicit activation
- `restorable_scopes`: the scopes that may participate in future recovery or startup restore flows
- `recoverable_scopes`: the last active restorable scopes Toolbox can attempt to recover later
- `restore_requires_identity`: whether restore requires stable host identity
- `restore_requires_explicit_request`: whether restore requires explicit host opt-in

`thread` scope is never restorable. That keeps thread-local tool exposure owned by the
host or conversation boundary instead of being silently widened by Toolbox on startup.

Toolbox now executes that restore policy through `restore_toolsets` and optional startup
restore in `create_server(..., restore_on_startup=True, stable_host_identity=...)`.
Startup restore only rehydrates `recoverable_scopes`, and only when the toolset's
identity and explicit-request gates allow it.

`activate_toolsets` now rejects scopes that are not declared in a toolset's
`supported_scopes`, so hosts get an immediate structured error instead of a silent policy
violation.

When a namespace is already active, adding another scope now reuses the existing live
runtime instead of reconnecting it. That keeps the mounted tool surface stable while the
requested scope is added to the toolset's `loaded_scopes`.

`deactivate_toolsets` now behaves like scope-reference removal: mounted tools stay
available until the last loaded scope is removed, and only the final scope removal closes
the downstream runtime.

Toolbox also no longer treats `refresh_toolsets` as an implicit activation path. Refresh
only runs for namespaces that already have at least one active scope, so an inactive
toolset cannot become mounted just because it was marked stale.

On service startup, Toolbox clears persisted `loaded_scopes` and keeps only
`recoverable_scopes` for later recovery. That means toolsets do not silently come back
mounted unless restore is explicitly requested or startup restore is enabled and allowed
by scope policy.

## Health

Toolbox now exposes `check_toolset_health` for bounded health probes against currently
active runtimes. A health check uses the live downstream session, not the cached contract,
and records the result back into toolset status.

Current health outcomes are:

- `healthy`: the runtime responded and the observed inventory still matches the cached contract
- `stale`: the runtime responded, but the observed inventory no longer matches the cached contract
- `failed`: the runtime could not be probed or was no longer connected

Health results are visible in `get_toolset_status` under the `health` block and summarized
in `list_toolsets` via `health_status` and `last_health_checked_at`.

Health-check failures now append structured audit events with operation `health_check`.
When a probe fails, Toolbox marks the toolset stale or failed with structured reasons like
`healthcheck_timeout`, `healthcheck_failed`, `healthcheck_runtime_missing`, or
`healthcheck_schema_changed`.

Hosts can then reconcile that state in two deliberate ways:

- `restore_toolsets`: reconnect previously active restorable scopes and remount tools when
  policy allows it
- `clear_stale_toolsets`: clear stale and health-failure markers without reconnecting the
  runtime

Recovery details are visible in `get_toolset_status` under the `recovery` block, including
`recoverable_scopes`, `last_restored_at`, and whether startup or explicit restore is
currently eligible for the namespace.

## Layout

```text
toolbox/
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── composition-workflows.md
│   ├── quickstart.md
│   └── skill-loader-mcp.md
├── toolbox/
│   ├── config.py
│   ├── fake_managed_server.py
│   ├── models.py
│   ├── program_runtime.py
│   ├── registry.py
│   ├── scope_manager.py
│   ├── server.py
│   ├── service.py
│   ├── skills_loader.py
│   ├── skills_server.py
│   ├── stdio_transport.py
│   ├── transport_manager.py
│   ├── transport_secrets.py
│   └── validation.py
├── tests/
├── HANDOFF.md
├── README.md
├── pyproject.toml
└── spec.med
```

## Run

```powershell
python -m toolbox.server
```

Toolbox stores its local state in `.toolbox/state.json` by default and seeds a fake managed
toolset manifest in `.toolbox/fake_toolset_manifest.json`.

The skill-loader MCP can be run separately:

```powershell
python -m toolbox.skills_server
```

## Quickstart

The shortest local proof-of-life path is:

1. Install the package and dev dependencies with `python -m pip install -e ".[dev]"`.
2. Run `python -m pytest -q` once.
3. Start Toolbox with `python -m toolbox.server`.
4. In your MCP host, call `toolbox_brief`, then `plan_toolset_activation` or `search_toolsets`.
5. Activate the chosen namespace such as `fake_stdio`.
6. Call `describe_mounted_tools`, then `run_tool_program` or `run_tool_batch`.
7. Call `deactivate_toolsets` when you are done.

For a concrete end-to-end example payload sequence, see [docs/quickstart.md](docs/quickstart.md).
For direct Codex and Toolbox-managed skill-loader registration examples, see
[docs/skill-loader-mcp.md](docs/skill-loader-mcp.md).

## Development

```powershell
python -m pytest
```

On Windows, the stricter subprocess-finalization lane is also part of the expected
verification bar:

```powershell
python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning
```

## Observability

The current control-plane observability surface is:

- `list_audit_events`: query recent lifecycle events without reading the state file directly
- `get_toolset_status`: inspect registration, activation, schema state, and the latest failure summary for one or more namespaces

Public registration/status transport metadata now redacts transport argument and
environment values. Hosts get `arg_count`, `has_args`, `env_keys`, and `has_env`, not
the raw `transport.args` or `transport.env` secrets.

Transport `args` and `env` are also protected at rest. Toolbox stores encrypted
transport bundles in `.toolbox/state.json` instead of persisting raw argument and
environment values directly, and older plaintext state files are migrated on load.

## State Compatibility

Toolbox now treats local state as an operational compatibility surface:

- the current persisted state document version is `2`
- older plaintext transport bundles are migrated automatically on load
- on Windows, protected transport bundles use DPAPI via the current user context
- on non-Windows platforms, Toolbox stores the encrypted bundle in `state.json` and the
  local AES-GCM key in `.toolbox/state.key`

If you change state structure again in a future milestone, keep a migration path and
document the new version explicitly instead of silently rewriting or dropping older
registrations.

Recorded events include successful and failed registration, activation, refresh,
deactivation, unregistration, and mounted-tool runtime failures such as call timeouts
or downstream transport exits.

## Introspection

The current agent-facing discovery surface is:

- `toolbox_brief`: get a compact recommended flow, active-toolset snapshot, and next actions
- `toolbox_overview`: see registered deferred capability categories and small examples
- `suggest_toolsets_for_task`: get a ranked shortlist of toolsets for a task description
- `plan_toolset_activation`: dry-run which toolsets should be mounted for a task and scope
- `get_toolset_guide`: load compact recipes and next actions for one selected toolset
- `audit_toolbox_catalog`: identify registrations with thin agent-facing metadata
- `search_toolsets`: search registered toolsets by metadata and agent-facing hints

The current contract inspection surface is:

- `inspect_cached_contracts`: inspect last-known-good cached tool contracts for one or more namespaces
- `inspect_mounted_contracts`: inspect currently mounted tool contracts for active namespaces
- `describe_mounted_tools`: inspect the lightest live per-tool summaries for pre-composition discovery
- `diff_cached_contracts`: diff the current cached contract against the previous stored snapshot
- `diff_mounted_contracts`: diff the current mounted contract against the previous stored snapshot

Both surfaces return compact schema summaries rather than full raw schemas. They are meant
to support deliberate host and model inspection before broader actions like refresh or
composition. The diff surfaces stay compact too: they report availability, current and
previous hashes, and only the changed tool summaries needed to understand the delta.

Toolbox also exposes `inspect_runtime_budgets` so hosts can inspect the current program
limits (`max_program_length_chars`, `max_ast_nodes`, `max_loop_iterations`,
`max_tool_calls`) and downstream timeout budgets before composing.

`run_tool_program` complements that by letting callers request only the specific local
variables and mounted-tool contract summaries they want back from a run, instead of
echoing the full initial context or reloading the full mounted inventory.

## Docs Map

- [docs/quickstart.md](docs/quickstart.md): fastest end-to-end local trial
- [docs/composition-workflows.md](docs/composition-workflows.md): progressive discovery, batch, and scripted composition examples
- [MCP_SERVER_RECOMMENDATIONS.md](MCP_SERVER_RECOMMENDATIONS.md): transcript-derived MCP server quality notes and Toolbox implementation status
- [.planning/phases/09-toolset-quality-intelligence/09-CONTEXT.md](.planning/phases/09-toolset-quality-intelligence/09-CONTEXT.md): planned next session for capability flags, quality scoring, lazy guidance, and examples
- [HANDOFF.md](HANDOFF.md): current operator/developer handoff

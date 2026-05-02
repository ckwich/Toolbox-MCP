# Toolbox MCP

Toolbox is a small MCP control plane for keeping an agent's default tool surface small.

Instead of loading every MCP server and every downstream tool schema all the time,
Toolbox stays loaded as a broker. Other toolsets can be registered, searched, checked,
activated, composed, refreshed, and deactivated only when the task actually needs them.

The goal is progressive discovery for MCP: rich enough for agents to find the right
capability, lazy enough that unused tool schemas do not burn context.

## Why This Exists

MCP servers are useful, but always-on tool loading has a cost. Large tool lists make
prompts heavier, increase latency, and make agents choose from capabilities that may not
matter for the current task.

Toolbox treats MCP connectivity as an on-demand lifecycle problem:

- Keep one small broker available.
- Search metadata before loading schemas.
- Mount downstream tools only when needed.
- Compose multiple downstream calls through one control-plane call.
- Unmount task-specific tools when the task is done.

## Current Status

This is an early local-first Python project. It is useful for experimenting with
agent-facing MCP orchestration, but it is not a hosted registry, a remote authorization
gateway, or a sandbox.

Current boundaries:

- Python 3.12+.
- Local stdio MCP toolsets only.
- Local JSON state in `.toolbox/`.
- Windows-tested; CI also runs the normal Python test suite.
- Downstream MCP servers still run with the permissions of the command you register.

## What Toolbox Does

- Registers downstream stdio MCP toolsets without activating them.
- Lets agents discover toolsets through compact metadata and task-oriented suggestions.
- Checks readiness before use, including metadata quality, required environment names,
  cached contracts, and optional stdio boot probes.
- Activates and deactivates toolsets by scope, mounting tools as `namespace.tool`.
- Caches tool contracts and diffs them across refreshes.
- Tracks health, stale state, recent failures, and lifecycle audit events.
- Imports repo-local catalog packs for repeatable toolset registration.
- Loads guidance bodies and composition examples only after explicit follow-up calls.
- Runs straight-line batches or constrained programs across mounted tools.
- Redacts public transport metadata so command arguments and environment values do not
  echo back through normal status and registration responses.

## The Basic Agent Flow

1. Call `toolbox_brief` for the cheapest orientation.
2. Call `suggest_toolsets_for_task` when a task may need hidden capabilities.
3. Call `plan_toolset_activation` before mounting anything.
4. Call `activate_toolsets` for the selected namespace and scope.
5. Use mounted tools through names like `fake_stdio.fake_status`.
6. Call `run_tool_batch` or `run_tool_program` when multiple tool calls should be composed.
7. Call `deactivate_toolsets` when the task-specific tools are no longer needed.

## Quickstart

Clone the repo and install it in editable mode:

```powershell
git clone https://github.com/ckwich/Toolbox-MCP.git
cd Toolbox-MCP
python -m pip install -e ".[dev]"
```

Run the test suite:

```powershell
python -m pytest -q
```

Start Toolbox as a stdio MCP server:

```powershell
python -m toolbox.server
```

No banner or prompt is expected in stdio mode. The process waits for an MCP host to speak
the protocol over stdin/stdout.

For an MCP host, configure Toolbox as a stdio server with this shape:

```json
{
  "command": "python",
  "args": ["-m", "toolbox.server"],
  "cwd": "C:/path/to/Toolbox-MCP",
  "env": {
    "PYTHONPATH": "C:/path/to/Toolbox-MCP"
  }
}
```

The repo seeds a fake managed toolset named `fake_stdio`, so you can exercise the full
discover -> activate -> call -> deactivate loop without configuring another MCP server.

See [docs/quickstart.md](docs/quickstart.md) for concrete host-call payloads.

## Key Concepts

### Toolsets

A toolset is a registered downstream MCP server. Toolbox stores metadata, transport
configuration, scope policy, cached schema summaries, health state, and optional guidance.

Inactive toolsets are discoverable but not mounted. Active toolsets expose downstream tools
under stable names like `namespace.tool_name`.

### Catalog Packs

Catalog packs are workspace-local JSON files that register one or more toolsets. They are
the preferred way to share repeatable toolset definitions without asking an agent to emit a
large `register_toolset` payload by hand.

Recommended pack flow:

1. `validate_catalog_pack(path)`
2. `import_catalog_pack(path, dry_run=true)`
3. `import_catalog_pack(path)`
4. `check_toolset_readiness(namespaces=[...], probe=true, refresh_cache=true)`

This repo includes [docs/catalog-packs/codex-skills.json](docs/catalog-packs/codex-skills.json),
which registers the optional read-only Codex skill-loader MCP as `codex_skills`.

See [docs/skill-loader-mcp.md](docs/skill-loader-mcp.md) for details.

### Guidance And Examples

Toolbox separates lightweight discovery from heavier instructions. Search, overview, and
status tools can tell an agent that guidance or examples exist, but the full bodies require
explicit calls:

- `load_toolset_guidance`
- `list_toolset_examples`
- `load_toolset_example`

This keeps default discovery compact while still letting toolset authors teach agents how
to use a capability correctly.

### Programmatic Tool Calling

Toolbox supports two composition surfaces:

- `run_tool_batch` for straight-line dataflow across mounted tools.
- `run_tool_program` for bounded Python-like scripts with variables, loops, conditionals,
  structured output access, and inline `call_tool("namespace.tool", ...)` calls.

Use these when several tool calls should happen inside one controlled MCP request instead
of making the model orchestrate each step through separate inference turns.

See [docs/composition-workflows.md](docs/composition-workflows.md) for examples.

## Important Control-Plane Tools

Discovery:

- `toolbox_brief`
- `toolbox_overview`
- `search_toolsets`
- `suggest_toolsets_for_task`
- `plan_toolset_activation`
- `get_toolset_guide`

Registration and readiness:

- `register_toolset`
- `unregister_toolsets`
- `validate_catalog_pack`
- `import_catalog_pack`
- `check_toolset_readiness`
- `inspect_toolset_quality`
- `audit_toolbox_catalog`

Lifecycle and health:

- `activate_toolsets`
- `deactivate_toolsets`
- `refresh_toolsets`
- `check_toolset_health`
- `restore_toolsets`
- `clear_stale_toolsets`
- `list_audit_events`

Contract inspection:

- `inspect_cached_contracts`
- `inspect_mounted_contracts`
- `describe_mounted_tools`
- `diff_cached_contracts`
- `diff_mounted_contracts`
- `inspect_runtime_budgets`

Composition:

- `run_tool_batch`
- `run_tool_program`
- `load_toolset_guidance`
- `list_toolset_examples`
- `load_toolset_example`

## State, Secrets, And Safety

Toolbox stores local state in `.toolbox/state.json`. The `.toolbox/` directory is ignored
by git and should stay local.

Public registration and status responses redact transport argument and environment values.
Hosts see shapes like `arg_count`, `has_args`, `env_keys`, and `has_env`, not raw secrets.

Transport `args` and `env` are also protected at rest. Current state documents use version
`2`, with encrypted transport bundles instead of plaintext transport values.

Encryption behavior:

- On Windows, protected transport bundles use DPAPI for the current user.
- On non-Windows platforms, Toolbox stores the encrypted bundle in `state.json` and the
  local AES-GCM key in `.toolbox/state.key`.

This protects accidental disclosure in normal Toolbox surfaces and local state files. It
does not turn untrusted downstream MCP servers into safe code.

## Development

Run the normal test suite:

```powershell
python -m pytest -q
```

On Windows, also run the strict subprocess-finalization lane:

```powershell
python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning
```

Useful focused checks:

```powershell
python -m pytest tests\test_catalog_packs.py -q
python -m pytest tests\test_skills_loader.py tests\test_skills_server.py tests\test_skills_server_entrypoint.py -q
python -m compileall toolbox tests
```

## Project Layout

```text
toolbox/
  server.py              FastMCP server and public control-plane tools
  service.py             registry, lifecycle, readiness, health, and composition logic
  transport_manager.py   stdio runtime ownership and mounted-tool forwarding
  stdio_transport.py     local stdio process transport wrapper
  registry.py            JSON state store and compatibility migration
  models.py              persisted and host-facing data models
  program_runtime.py     constrained programmatic tool-calling runtime
  skills_loader.py       read-only Codex SKILL.md discovery/loading helpers
  skills_server.py       optional Codex skill-loader MCP server
  fake_managed_server.py seeded fake MCP server for tests and demos
docs/
  quickstart.md
  composition-workflows.md
  skill-loader-mcp.md
  catalog-packs/codex-skills.json
tests/
```

## Docs Map

- [docs/quickstart.md](docs/quickstart.md): fastest local proof-of-life path.
- [docs/composition-workflows.md](docs/composition-workflows.md): progressive discovery,
  batch composition, and scripted composition examples.
- [docs/skill-loader-mcp.md](docs/skill-loader-mcp.md): optional Codex skill-loader MCP.
- [MCP_SERVER_RECOMMENDATIONS.md](MCP_SERVER_RECOMMENDATIONS.md): transcript-derived MCP
  server quality notes and implementation status.
- [HANDOFF.md](HANDOFF.md): current operator/developer handoff.

## Roadmap Direction

The north star is an agent-first MCP toolbox:

- Better catalog packs for common local MCP servers.
- Stronger readiness and quality checks before activation.
- More executable composition examples.
- More explicit task, trigger, streaming, and reference-result metadata as the MCP
  ecosystem standardizes those patterns.

The bias is deliberate: keep the always-on broker small, make hidden capabilities easy to
find, and load expensive details only when the agent actually needs them.

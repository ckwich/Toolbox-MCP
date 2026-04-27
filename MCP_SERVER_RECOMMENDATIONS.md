# MCP Server Recommendations

Source context:

- User-provided transcript excerpt about progressive discovery and programmatic tool calling.
- Official roadmap cross-check: https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/
- Local repo context: Toolbox is a metadata-first MCP control plane for registered toolsets, lazy activation, contract caching, health checks, and composition.

## Summary

The transcript reinforces the direction Toolbox is already moving in: MCP server work is shifting from small local wrappers to production integration surfaces. The important design pressure is no longer "can this server expose a tool?" but "can an agent discover, activate, understand, compose, monitor, and safely operate this server without flooding context or guessing?"

For Toolbox, the strongest takeaways are:

1. Make registered servers discoverable through compact metadata before schemas.
2. Treat server guidance and skills as first-class artifacts.
3. Prefer structured outputs so tools compose through code.
4. Keep progressive discovery as the default client/server pattern.
5. Design now for tasks, triggers, streaming, and enterprise auth without depending on immature client support.

## Implementation Status

The near-term, agent-facing subset is now implemented across Phase 7 and Phase 8:

1. Compact registered server discovery: `toolbox_overview`, `search_toolsets`, and `suggest_toolsets_for_task`.
2. Rich registration metadata: category, aliases, examples, recipes, activation hint, cost hint, latency hint, and trust hint.
3. First-call session orientation: `toolbox_brief`.
4. Dry-run activation planning: `plan_toolset_activation`.
5. Lazy per-toolset guidance: `get_toolset_guide`.
6. Catalog quality checks: `audit_toolbox_catalog`.
7. Composition-ready operation: `describe_mounted_tools`, `run_tool_batch`, `run_tool_program`, and JSON-safe structured result envelopes.

Future protocol-shaped ideas such as tasks, triggers, streaming, and native skills-over-MCP remain intentionally deferred until client and SDK support is mature enough to verify against.

## What Applies Directly To Toolbox

### Metadata-first registered server discovery

Toolbox should expose every registered server as a compact catalog entry. A future session should be able to answer "what can I use?" without activating every server or loading every downstream schema.

Recommended registered toolset summary fields:

- `namespace`
- `title`
- `description`
- `tags`
- `registered`
- `loaded`
- `loaded_scopes`
- `supported_scopes`
- `restorable_scopes`
- `transport_kind`
- `auth_required`
- `health_status`
- `stale`
- `schema_hash`
- `tool_count`
- `last_refresh_at`
- `last_health_checked_at`
- `capability_flags`
- `recommended_entrypoints`
- `usage_guidance_available`

Suggested capability flags:

- `has_tools`
- `has_resources`
- `has_prompts`
- `has_skills`
- `supports_tasks`
- `supports_triggers`
- `supports_streaming`
- `supports_structured_outputs`
- `supports_health_check`
- `supports_batch_composition`
- `requires_auth`
- `requires_workspace_root`

### Tool search before tool load

Toolbox should keep its always-loaded control surface small. Discovery tools should return small records, not raw schemas. Schema-heavy or body-heavy data should require an explicit follow-up call.

Useful surfaces:

- `search_registered_toolsets(query, tags, capability_flags)`
- `list_registered_toolsets(scope, include_inactive)`
- `describe_registered_toolset(namespace)`
- `describe_registered_capabilities(namespace)`
- `inspect_cached_contracts(namespace)`
- `describe_mounted_tools(namespace)`

The default result should help an agent choose the next tool, not execute the final task.

### Server guidance as a first-class artifact

The transcript's discussion of skills-over-MCP maps directly to the problem we just solved with `codex_skills`: agents need operational guidance, not only tool names.

For each serious MCP server, prefer a bundled guidance artifact with:

- what the server is for
- when to use it
- when not to use it
- primary workflows
- tool call sequence examples
- required auth/setup
- workspace/root assumptions
- validation commands
- known hazards
- recovery steps

Toolbox can index these as `usage_guidance_available = true` and expose them lazily through a tool such as `load_toolset_guidance(namespace)`.

### Structured outputs for composition

The transcript calls out composition through code and structured outputs as the MCP equivalent of shell pipes. Toolbox already has this shape with `run_tool_program`.

To make future MCP servers composition-ready:

- return JSON objects, not prose blobs
- include stable top-level fields
- make success and failure explicit
- include item arrays for iterable data
- include next cursors for pagination
- include warnings separately from hard errors
- avoid leaking raw secrets or full transport config

Recommended output envelope:

```json
{
  "success": true,
  "error": null,
  "items": [],
  "count": 0,
  "next_cursor": null,
  "warnings": [],
  "metadata": {}
}
```

Toolbox should prefer downstream servers that return this kind of structure, and its discovery surfaces can report whether a tool appears composition-ready.

### Lifecycle state is part of the product

Production MCP usage needs more than tool invocation. Registered servers need lifecycle visibility.

Toolbox should continue treating these as first-class:

- registration
- activation
- deactivation
- refresh
- health check
- stale detection
- schema diffing
- last-known-good contract preservation
- recent audit history
- recoverable scopes

When something fails, the agent should know which gate failed:

- not registered
- registered but inactive
- active but unhealthy
- active but stale
- auth missing
- workspace root missing
- schema changed
- downstream call failed

## MCP Server Quality Standard

Use this as a lightweight checklist when building or accepting new MCP servers into Toolbox.

### Discovery

- Has a short title and useful description.
- Has tags that describe domain and risk.
- Has compact metadata available without activation when possible.
- Does not require loading full schemas for basic discovery.
- Provides usage guidance or a skill.

### Tool Design

- Tool names are stable and action-oriented.
- Tool descriptions are specific enough for tool search.
- Inputs use typed schemas with clear required fields.
- Outputs are structured JSON.
- Errors are structured and machine-readable.
- Long result sets are paginated or summarized.
- Large raw payloads are behind explicit load/fetch calls.

### Safety

- Secrets are never returned in control-plane responses.
- Auth requirements are visible without exposing credentials.
- Dangerous operations are clearly named and require explicit inputs.
- Read and write operations are separate tools.
- Workspace roots are explicit and verifiable.
- Path handling is allowlisted where possible.

### Lifecycle

- Server can be health-checked.
- Schema hash is stable across equivalent starts.
- Schema changes are detectable.
- Startup failures return actionable errors.
- Long-running operations expose status and cancellation if needed.
- Tool calls have bounded timeouts.

### Composition

- Results can be consumed by `run_tool_program`.
- IDs, cursors, and item collections are stable.
- Human summaries are separate from machine fields.
- Tool outputs are small enough by default.
- The server provides examples of multi-step workflows.

### Documentation

- Includes install/run instructions.
- Includes registration payload or `config.toml` snippet.
- Includes local smoke test.
- Includes known client limitations.
- Includes validation commands for future agents.

## Near-term Toolbox Opportunities

### 1. Registered server discovery tools

Add or improve tools that make all registered servers discoverable without activation.

Candidate tools:

- `search_registered_toolsets`
- `describe_registered_toolsets`
- `list_registered_capabilities`
- `load_toolset_guidance`

These should use the state file, cached contracts, metadata, and audit state.

### 2. Guidance indexing

Add a convention for guidance next to a managed registration.

Possible sources:

- registered metadata field such as `guidance_path`
- cached MCP skill entries if/when supported
- `docs/*.md` hints inside local repos
- a server-provided `usage` or `skills` capability

Toolbox should keep the guidance body lazy-loaded.

### 3. Capability scoring

Expose a compact quality summary for each registered server.

Example:

```json
{
  "namespace": "codex_skills",
  "quality": {
    "has_cached_contract": true,
    "has_health_check": true,
    "has_guidance": true,
    "structured_output_ratio": 1.0,
    "dangerous_tool_count": 0,
    "requires_auth": false
  }
}
```

This gives agents a way to prefer safer, better-described servers.

### 4. Composition examples

Let a registered server provide example programs or batches that Toolbox can expose.

Example surfaces:

- `list_toolset_examples(namespace)`
- `load_toolset_example(namespace, example_id)`

This would make `run_tool_program` easier to use reliably.

### 5. Prepared future support for tasks/triggers/streaming

Do not build against speculative protocol details yet. Instead, reserve clean metadata fields and internal concepts:

- tasks: `supports_tasks`, `task_lifecycle`
- triggers: `supports_triggers`, `trigger_types`
- streaming: `supports_streaming`, `streaming_result_types`
- references: `supports_reference_results`

This keeps Toolbox ready without making the current implementation brittle.

## What Not To Do Yet

- Do not eagerly activate every registered server at startup.
- Do not dump every downstream tool schema into the default context.
- Do not treat task/trigger/streaming roadmap items as stable implementation requirements until the current client and SDK support is proven.
- Do not merge all server guidance into global `AGENTS.md`; use global guidance only as a breadcrumb to Toolbox discovery.
- Do not expose raw transport args/env in discovery responses.
- Do not assume `cwd` is enough for workspace-aware servers; root discovery should be explicit.

## Recommended Next Step

For the active Toolbox work on making registered servers discoverable, implement the smallest useful vertical slice:

1. Add a compact registered server summary model.
2. Add a search/list tool over registered summaries.
3. Add a detailed describe tool for one namespace.
4. Include cached contract status and capability flags.
5. Keep raw contracts and guidance behind explicit follow-up calls.
6. Add tests with inactive, active, stale, auth-required, and cached-contract cases.

That would turn the transcript's roadmap ideas into a concrete Toolbox feature without betting on future MCP protocol changes too early.

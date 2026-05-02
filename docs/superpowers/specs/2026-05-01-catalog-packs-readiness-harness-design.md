# Catalog Packs and Readiness Harness Design

## Goal
Build an agent-first way to import rich Toolbox catalog registrations from declarative JSON packs, then prove those registrations are actually usable through the same stdio boot and `list_tools` path used by activation.

## Non-Goals
- No UI. Toolbox remains an agent-facing MCP control plane.
- No auto-activation. Readiness may transiently probe inactive toolsets, but it must not leave them mounted.
- No package manager. A catalog pack is a registration manifest, not an installer or dependency resolver.
- No plaintext secret exposure. Pack validation and readiness results must not echo transport args or env values.

## Architecture
Add a focused catalog-pack layer that validates JSON manifests into existing registration payloads. The service imports each pack entry by reusing the same `ToolsetRecord` model and registration semantics used by `register_toolset`; it does not introduce another persistence format.

Add a readiness harness that checks registered toolsets for metadata quality, required host environment names, cached contract presence, and downstream stdio boot/list-tools health. For inactive toolsets, the harness opens a temporary runtime and closes it without swapping it into the mounted runtime table. For active toolsets, it probes the existing runtime. Optionally, successful readiness checks may update the schema cache.

## Catalog Pack Shape
The manifest is JSON with this shape:

```json
{
  "version": 1,
  "name": "local-dev-toolsets",
  "description": "Developer MCP toolsets for this workstation.",
  "toolsets": [
    {
      "namespace": "example_docs",
      "title": "Example Docs",
      "description": "Search example project documentation.",
      "transport": {
        "kind": "stdio",
        "command": "python",
        "args": ["-m", "example.docs_server"],
        "cwd": "C:/Dev/Example"
      },
      "tags": ["docs"],
      "category": "docs",
      "required_env": ["EXAMPLE_API_KEY"],
      "activation_hint": "Activate when a task needs Example project documentation.",
      "cost_hint": "low",
      "latency_hint": "low",
      "trust_hint": "local"
    }
  ]
}
```

`required_env` records host environment variable names needed by the transport or downstream toolset. It is metadata only: Toolbox reports missing names, but never stores or emits values.

## MCP Tools
- `validate_catalog_pack(path)`: Load and validate a pack file without changing state.
- `import_catalog_pack(path, update_existing=True, dry_run=False)`: Register or update toolsets from a pack.
- `check_toolset_readiness(namespaces=None, probe=True, refresh_cache=False)`: Report whether registered toolsets are metadata-ready, env-ready, cache-ready, and boot-ready.

## Data Flow
1. Agent calls `validate_catalog_pack` on a workspace-local JSON file.
2. Agent calls `import_catalog_pack` after validation looks clean.
3. Toolbox normalizes pack entries into `ToolsetRecord` instances and writes them through `JsonStateStore`.
4. Agent calls `check_toolset_readiness`.
5. Toolbox reports compact readiness results. It redacts transport values and only names missing env vars.
6. Agent uses `toolbox_brief`, `suggest_toolsets_for_task`, and `plan_toolset_activation` as before.

## Error Handling
- Invalid manifest JSON returns a structured `invalid_catalog_pack` error.
- Unsupported pack version returns `unsupported_catalog_pack_version`.
- Duplicate namespaces inside one pack return `duplicate_catalog_pack_namespace`.
- Existing registrations are skipped unless `update_existing=True`.
- Missing required environment variables are warnings unless boot probing also fails.
- Probe failures are recorded in readiness output, but inactive toolsets remain inactive.

## Testing
- Unit tests validate pack shape, duplicate namespace rejection, dry-run behavior, import/update behavior, and required-env visibility.
- Readiness tests cover active and inactive toolsets, missing env warnings, successful temporary boot, optional cache refresh, and failed boot.
- Server tests prove the new tools are exposed over MCP and return structured payloads.
- Existing redaction tests must continue to prove args/env values do not leak through public responses or state.

## Acceptance Criteria
- Agents can import a pack without hand-writing a large `register_toolset` call.
- Readiness proves boot/list-tools health without mounting inactive toolsets.
- Required env names are visible; values are never visible.
- The default discovery surface remains compact.
- The full test suite passes.

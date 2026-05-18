# Toolbox Handoff

## Current Status

Toolbox is a working control-plane MCP for progressive discovery and on-demand toolset
activation. The core v1 roadmap and a post-v1 polish pass are complete.

Today the repo includes:

- registration, activation, refresh, deactivation, and unregistration of managed stdio toolsets
- declarative workspace-local catalog-pack validation and import
- readiness checks for metadata, required env names, cached contracts, and stdio boot/list-tools probes
- mounted downstream tools under `namespace.tool`
- cached and mounted contract inspection plus diffing
- scoped activation semantics across `thread`, `session`, and `global`
- bounded health checks, recovery flows, and stale-state reconciliation
- batch and constrained scripted composition
- agent-facing brief, activation planning, per-toolset guides, recipes, and catalog audit tooling
- recent audit history and recent-failure summaries
- protected transport-secret persistence and plaintext-state migration
- a separate read-only Codex skill-loader MCP for listing, loading, searching, and validating `SKILL.md` files
- OS-agnostic catalog packs through portable base transports plus optional host variants
- cross-platform CI for the main verification lanes

## First Run

1. Install dependencies:
   `python -m pip install -e ".[dev]"`
2. Verify the repo locally:
   `python -m pytest -q`
3. On Windows, also run the strict warning lane:
   `python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning`
4. Start Toolbox:
   `python -m toolbox.server`
5. In your MCP host, call `toolbox_brief` first to orient, then follow
   [docs/quickstart.md](docs/quickstart.md) to exercise the seeded `fake_stdio` toolset.
6. To seed real toolsets, create a workspace-local catalog pack, call
   `validate_catalog_pack`, preview with `import_catalog_pack(..., dry_run=true)`,
   import it, then call `check_toolset_readiness(..., probe=true, refresh_cache=true)`.

## Key Docs

- Product/spec source: [spec.med](spec.med)
- Main repo overview: [README.md](README.md)
- Fast local trial: [docs/quickstart.md](docs/quickstart.md)
- Host composition patterns: [docs/composition-workflows.md](docs/composition-workflows.md)
- Codex skill-loader MCP registration: [docs/skill-loader-mcp.md](docs/skill-loader-mcp.md)
- Planning spine: [.planning/PROJECT.md](.planning/PROJECT.md)

## Verification Standard

The current local verification bar is:

- `python -m pytest -q`
- `python -m pytest tests/test_skills_loader.py tests/test_skills_server.py tests/test_skills_server_entrypoint.py -q`
- `python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning`
- `python -m compileall toolbox tests`

The same expectations now live in `.github/workflows/ci.yml` for Linux and Windows.

## State and Secrets

Toolbox persists local state in `.toolbox/state.json`.

- Public control-plane responses do not return raw `transport.args` or `transport.env`.
- Transport `args` and `env` are protected at rest before they land in `state.json`.
- On Windows, protection uses DPAPI under the current user.
- On non-Windows platforms, Toolbox stores encrypted bundles in `state.json` and the
  local AES-GCM key in `.toolbox/state.key`.
- Older plaintext transport bundles are migrated on load.
- Catalog packs can use `host_variants` for macOS, Linux, Windows, POSIX/Unix, or
  default host-specific overrides. Validation/import resolves the current-host variant
  before persisting a toolset.

If future work changes the state document again, treat it like a compatibility surface:
add migration logic, test it, and document the new version clearly.

## Deferred Next Work

The finished milestone intentionally stopped short of:

- remote transports beyond stdio
- connector-backed registrations
- unrestricted execution/runtime sandboxes
- broader host-specific integrations

## Next Planned Session

Phase 9 and the first Phase 10 slice are implemented.

- Start here: [.planning/phases/09-toolset-quality-intelligence/09-CONTEXT.md](.planning/phases/09-toolset-quality-intelligence/09-CONTEXT.md)
- Phase 9 added derived capability flags, quality summaries, lazy guidance loading, and explicit example payload loading.
- Phase 10 added catalog-pack validation/import and readiness checks.
- Phase 10 spec: [docs/superpowers/specs/2026-05-01-catalog-packs-readiness-harness-design.md](docs/superpowers/specs/2026-05-01-catalog-packs-readiness-harness-design.md)
- Phase 10 plan: [docs/superpowers/plans/2026-05-01-catalog-packs-readiness-harness.md](docs/superpowers/plans/2026-05-01-catalog-packs-readiness-harness.md)

The right posture is still deliberate and test-first: do not reopen completed Phase 7/8
discovery work ad hoc, and do not implement speculative tasks/triggers/streaming behavior
until client and SDK support is proven.

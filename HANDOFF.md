# Toolbox Handoff

## Current Status

Toolbox is a working control-plane MCP for progressive discovery and on-demand toolset
activation. The core v1 roadmap and a post-v1 polish pass are complete.

Today the repo includes:

- registration, activation, refresh, deactivation, and unregistration of managed stdio toolsets
- mounted downstream tools under `namespace.tool`
- cached and mounted contract inspection plus diffing
- scoped activation semantics across `thread`, `session`, and `global`
- bounded health checks, recovery flows, and stale-state reconciliation
- batch and constrained scripted composition
- recent audit history and recent-failure summaries
- protected transport-secret persistence and plaintext-state migration
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
5. Follow [docs/quickstart.md](docs/quickstart.md) to exercise the seeded `fake_stdio` toolset.

## Key Docs

- Product/spec source: [spec.med](spec.med)
- Main repo overview: [README.md](README.md)
- Fast local trial: [docs/quickstart.md](docs/quickstart.md)
- Host composition patterns: [docs/composition-workflows.md](docs/composition-workflows.md)
- Planning spine: [.planning/PROJECT.md](.planning/PROJECT.md)

## Verification Standard

The current local verification bar is:

- `python -m pytest -q`
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

If future work changes the state document again, treat it like a compatibility surface:
add migration logic, test it, and document the new version clearly.

## Deferred Next Work

The finished milestone intentionally stopped short of:

- remote transports beyond stdio
- connector-backed registrations
- unrestricted execution/runtime sandboxes
- broader host-specific integrations

If the next milestone starts, the right posture is to plan it as a new deliberate phase,
not to reopen the completed roadmap ad hoc.

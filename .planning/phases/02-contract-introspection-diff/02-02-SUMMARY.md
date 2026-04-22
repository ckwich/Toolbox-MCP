# Plan 02-02 Summary

## Completed

Implemented the second Phase 2 introspection slice for Toolbox MCP:

- Added compact contract diff models in `toolbox/models.py`
- Added previous-schema snapshot persistence in `toolbox/registry.py`
- Added `diff_cached_contracts` and `diff_mounted_contracts` in `toolbox/service.py`
- Exposed both diff surfaces through the MCP control plane in `toolbox/server.py`
- Added service-level and MCP-level regression coverage in `tests/test_service.py`
- Updated README and planning artifacts to reflect completed Phase 2 behavior

## Key Decisions Carried Into Code

- Contract diffing reuses stored previous snapshots instead of relying on hash-only history
- Cached and mounted diffing are separate deliberate surfaces, mirroring the inspection split from `02-01`
- Diff output stays compact: availability, current and previous hashes, changed tool names, and only the changed tool summaries
- Missing previous snapshots are reported explicitly via `previous_available` and `diff_available` instead of pretending the first observed contract is a normal delta

## Verification

- `python -m pytest -q tests/test_service.py -k "diff_cached_contracts or diff_mounted_contracts or server_diff_surfaces"` -> `4 passed`
- `python -m pytest -q` -> `35 passed in 23.07s`
- `python -m compileall toolbox tests` -> passed
- Re-read the changed `models.py`, `registry.py`, `service.py`, `server.py`, `tests/test_service.py`, `README.md`, and planning artifacts after verification to confirm snapshot rotation, diff availability semantics, and compact change summaries stayed aligned
- Windows still emits an intermittent asyncio subprocess finalization warning after pytest even though the suite is green; this is an existing cleanup issue, not a diff-slice regression

## Outcome

Hosts and models can now request deliberate current-vs-previous contract diffs for both cached and mounted tool surfaces without broadening the default control-plane context.

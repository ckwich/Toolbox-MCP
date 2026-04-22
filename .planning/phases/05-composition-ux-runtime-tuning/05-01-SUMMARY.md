# 05-01 Summary: Mounted-Tool Description and Runtime-Budget Inspection

## Delivered

`05-01` added two new composition-prep control-plane surfaces:

- `describe_mounted_tools`: a cheap live per-tool description surface keyed by mounted
  names like `namespace.tool`, with compact schema summaries and structured missing
  reasons for unknown or inactive filters
- `inspect_runtime_budgets`: explicit inspection of current program limits and transport
  timeout budgets so hosts can shape composition requests intentionally

## Implementation Notes

- Added typed models in `toolbox/models.py`
- Added budget introspection helpers in `toolbox/program_runtime.py` and
  `toolbox/transport_manager.py`
- Added the service/control-plane surface in `toolbox/service.py` and
  `toolbox/server.py`
- Added service and MCP regression coverage in `tests/test_service.py`
- Updated `README.md` and the Phase 5 planning/state artifacts

## Verification

- `python -m pytest -q tests/test_service.py -k "describe_mounted_tools or inspect_runtime_budgets or precomposition_description"` passed
- `python -m pytest -q` passed
- `python -m compileall toolbox tests` passed
- Changed code paths were re-read after the green run

## Requirements

- `COMP-01`: completed by the mounted-tool description surface
- `COMP-04`: completed by the runtime-budget inspection surface

## Next Step

Proceed to `05-02`: host-facing composition docs, examples, and helper affordances.

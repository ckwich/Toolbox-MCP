# Phase 5 Context: Composition UX and Runtime Tuning

## Why This Phase Exists

Toolbox already proves the core control-plane loop: search, activate, inspect, compose,
refresh, recover, and unload managed toolsets. Phase 5 is about making that loop easier
for hosts and models to use deliberately without widening the always-on surface.

The key gap entering this phase was ergonomic, not architectural:

- `inspect_mounted_contracts` is useful but still toolset-oriented and heavier than a
  composition caller needs when it just wants to know which mounted tools are live.
- Program/runtime limits existed in code, but hosts had no explicit surface for seeing
  them before writing a batch or scripted composition request.

## Phase Decomposition

- `05-01`: add cheap mounted-tool descriptions and runtime-budget inspection surfaces
- `05-02`: add host-facing composition docs, examples, and helper affordances on top of
  those new discovery surfaces

## Baseline Entering 05-01

- Mounted tools are already exposed dynamically under `namespace.tool`.
- Cached and mounted contract inspection/diff surfaces already exist.
- `run_tool_batch` and `run_tool_program` already provide bounded composition execution.
- Program runtime limits are currently hard-coded in `ToolProgramRuntime`.
- Transport timeout limits are currently configured on `TransportManager`.

## Guardrails

- Keep the new discovery surfaces compact and composition-oriented.
- Reuse the existing schema-summary model rather than introducing raw schema dumps.
- Do not broaden execution capability in this phase; improve discovery and inspection only.
- Keep README and `.planning` in sync with shipped behavior.

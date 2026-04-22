# 06 Context: Release and Operability Polish

## Why This Phase Exists

The v1 roadmap is complete, and the codebase already covers the core control-plane,
scope, health, recovery, and composition behaviors. The remaining gap is not product
surface area. It is release readiness: preserving current guarantees across platforms,
making the first run obvious, and replacing stale bootstrap-era docs with a current
operational handoff.

## Scope

This phase is intentionally narrow:

- keep the validated runtime and control-plane behavior unchanged
- add automated cross-platform verification
- document the shortest successful first-run path
- document state compatibility and transport-secret handling clearly
- replace stale handoff guidance with a current operator/developer handoff

## Non-Goals

- new lifecycle or composition features
- new transports beyond stdio
- new domain servers or host integrations
- widening the always-on MCP surface

## Steelman

If we were forced to pick the smallest useful post-v1 polish slice, this is it:

1. CI is the cheapest durable protection for the hard-won Windows/Linux verification
   story, especially around the strict unraisable-warning lane.
2. Quickstart guidance removes unnecessary friction when someone tries Toolbox for the
   first time and tests the fake managed toolset flow.
3. State-version and secret-storage notes are now part of the contract because the repo
   has migration behavior and protected transport persistence.
4. The current `HANDOFF.md` still describes the repo as if nothing has been built. That
   is now actively misleading.

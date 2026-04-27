# Phase 9 Context: Toolset Quality Intelligence

## Why This Phase Exists

Phase 7 made registered toolsets discoverable. Phase 8 made Toolbox easier for agents to
use deliberately. The remaining useful material in `MCP_SERVER_RECOMMENDATIONS.md` is
about quality: helping an agent choose the safest, best-described, most composable
toolset without activating every server or loading every downstream schema.

This phase should convert that recommendation layer into compact derived metadata and
lazy follow-up surfaces.

## What Is Already Done

- `toolbox_overview`, `search_toolsets`, and `suggest_toolsets_for_task` expose compact
  registered toolset discovery.
- Registrations support category, aliases, examples, recipes, activation hint, cost hint,
  latency hint, and trust hint.
- `toolbox_brief`, `plan_toolset_activation`, `get_toolset_guide`, and
  `audit_toolbox_catalog` give agents a passive operating path.
- `inspect_cached_contracts`, `inspect_mounted_contracts`, `describe_mounted_tools`,
  `run_tool_batch`, and `run_tool_program` already cover compact introspection and
  composition execution.
- Public transport metadata and persisted transport secrets are already hardened.

## What Phase 9 Should Add

- Derived `capability_flags` for registered toolsets.
- A compact `quality` summary that explains guidance coverage, composition readiness,
  health/state posture, auth/workspace assumptions, and missing catalog metadata.
- Lazy guidance-source metadata such as a constrained `guidance_path`, without putting
  entire markdown bodies into overview/search results.
- Composition example metadata and explicit example loading by id.
- Inert future-protocol metadata for tasks, triggers, streaming, and reference results.

## Boundaries

- Do not auto-activate toolsets.
- Do not dump raw downstream schemas into default discovery.
- Do not execute composition examples from the loader; list and load them only.
- Do not implement real task/trigger/streaming behavior until current MCP client and SDK
  support is proven.
- Do not allow arbitrary guidance paths. Any path-based guidance should be repo/workspace
  bounded and should avoid reading secrets or generated state.

## Recommended Start

Start with `09-01`: capability flags and quality summaries. This creates the data model
that `09-02` can build on for lazy guidance and composition examples.

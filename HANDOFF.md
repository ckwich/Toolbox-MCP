# Toolbox Handoff

## What Was Written

- Main specification: [spec.med](C:/Dev/Toolbox/spec.med)
- Host composition guide: [docs/composition-workflows.md](C:/Dev/Toolbox/docs/composition-workflows.md)

## Project Intent

Build a small control-plane MCP server that manages the lifecycle of other MCP toolsets.
The point is to support lazy loading, reduce unnecessary tool exposure, and refresh stale
toolsets in-place without forcing a new conversation.

## Locked Decisions

- This is a separate MCP, not part of any domain server.
- The always-loaded surface should stay very small.
- The primary workflow is metadata-first, then activation on demand.
- Refresh must be atomic and preserve the last-known-good contract on failure.
- Scope matters and should support at least `thread`, `session`, and `global`.

## Recommended First Build Order

1. Scaffold the repo and FastMCP server.
2. Implement the registry model and JSON persistence.
3. Implement `search_toolsets`, `list_toolsets`, and `get_toolset_status`.
4. Add activation for one managed stdio MCP server.
5. Add schema hashing and cache persistence.
6. Add refresh with schema diff reporting.
7. Add safe deactivation with in-flight call protection.

## Suggested First Milestone

Aim for a narrow vertical slice:

- one registered fake MCP server
- one activation path
- one refresh path
- one deactivation path
- one thread-scoped state model

That will prove the architecture before expanding to multiple transports or richer host integration.

## Key Risks

- Letting activation or refresh partially succeed and exposing a broken contract
- Mixing host scope state and Toolbox scope state without a clear source of truth
- Making the control-plane tool surface too large and defeating the token-saving goal
- Over-designing registration or auth before the activation/refresh core is proven

## Recommended Technical Shortcuts

- Use JSON storage first unless concurrent writers become an immediate requirement.
- Start with stdio transport only.
- Diff schemas by normalized hash, not by raw text.
- Keep stale detection passive in v1, then add optional heartbeat checks later.

## Good v1 Done State

The first version is good if:

- it can describe known toolsets cheaply
- activate a toolset on demand
- detect and refresh a stale toolset
- report schema changes clearly
- deactivate safely

Everything else can wait until after that loop is working end to end.

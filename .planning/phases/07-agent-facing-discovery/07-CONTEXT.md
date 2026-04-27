# Phase 7 Context: Agent-Facing Discovery

## Problem

Toolbox can defer and mount toolsets, but an agent still needs a cheap reason to ask
Toolbox in the first place. If a useful capability such as a skill loader is registered
behind Toolbox, the agent may not search for it unless the user hints that it exists.

## Direction

Add a small always-visible orientation surface and richer toolset registration metadata.
The agent should be able to discover capability categories, examples, and relevant
toolsets by task intent without pulling full downstream schemas into context.

## Constraints

- Keep Toolbox's default MCP surface compact.
- Do not auto-activate toolsets from suggestions.
- Do not expose raw transport arguments, environment values, or full downstream schemas
  through the new discovery helpers.
- Preserve existing registrations by adding defaults for any new metadata fields.

## Target

Phase 7 is complete when an agent can call `toolbox_overview` to understand what
categories are available, and `suggest_toolsets_for_task` to get a short ranked list
of deferred toolsets for a task such as "load a skill" or "scan code for security".

# Phase 8 Context: Agent Guidance Affordances

## Why This Phase Exists

Phase 7 made deferred toolsets discoverable by metadata, but a fresh agent still needed
chat context to know the intended operating pattern: orient, shortlist, plan activation,
mount narrowly, inspect, compose, and clean up.

This phase turns that pattern into passive Toolbox affordances. The goal is not to add
more always-on downstream tools. The goal is to help an agent use the existing control
plane correctly with fewer tokens and fewer guesses.

## Design Constraints

- Guidance tools must never auto-activate toolsets.
- Guidance tools must not dump downstream raw schemas.
- Guidance output must not expose transport arguments, environment values, or other secrets.
- Suggestions and plans should prefer meaningful task terms, not stopword matches.
- Per-toolset usage help should be compact metadata, with heavier guidance remaining a
  future lazy-load concern.

## Resulting Surface

- `toolbox_brief`: compact first-call orientation, active-toolset snapshot, suggestions,
  and next actions.
- `plan_toolset_activation`: dry-run plan for selected namespaces, already loaded
  namespaces, skipped candidates, and follow-up inspection.
- `get_toolset_guide`: compact recipes, examples, when-to-use hints, and next actions for
  one selected namespace.
- `audit_toolbox_catalog`: metadata linting for registrations that are too thin for
  reliable agent discovery.

## Non-Goals

- No automatic activation.
- No speculative implementation of future MCP tasks, triggers, streaming, or skills-over-MCP semantics.
- No broad ingestion of markdown guidance into the always-on control surface.

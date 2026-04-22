# Host Composition Workflows

Toolbox is designed so hosts keep the always-on surface small, then opt into richer
discovery and composition only when they need it. This guide shows the intended flow.

## Choose The Smallest Helpful Surface

- Use `search_toolsets` when you only need metadata and candidate namespaces.
- Use `activate_toolsets` when the host has decided a toolset should become callable.
- Use `describe_mounted_tools` when you need the cheapest live per-tool view before
  generating a batch or a scripted program.
- Use `inspect_runtime_budgets` before generating a larger scripted program so the host
  knows the current length, AST, loop, and tool-call limits.
- Use `run_tool_batch` for straight-line dataflow across a few mounted tool calls.
- Use `run_tool_program` when you need loops, conditionals, or local data shaping.

## Flow 1: Progressive Discovery To Batch Composition

1. Discover candidate toolsets:

```json
{
  "tool": "search_toolsets",
  "arguments": {
    "query": "fake status metrics"
  }
}
```

2. Activate the chosen toolset for the host scope:

```json
{
  "tool": "activate_toolsets",
  "arguments": {
    "namespaces": ["fake_stdio"],
    "scope": "thread"
  }
}
```

3. Inspect the mounted tools cheaply:

```json
{
  "tool": "describe_mounted_tools",
  "arguments": {
    "namespaces": ["fake_stdio"]
  }
}
```

4. Compose a straight-line batch:

```json
{
  "tool": "run_tool_batch",
  "arguments": {
    "steps": [
      {
        "id": "status",
        "tool": "fake_stdio.fake_status"
      },
      {
        "id": "metrics",
        "tool": "fake_stdio.fake_metrics",
        "arguments": {
          "arguments": {
            "prior_status": {"$from": "status.response.status"},
            "prior_version": {"$from": "status.response.version"}
          }
        }
      }
    ],
    "final_step": "metrics"
  }
}
```

Use batch mode when the flow is linear and references between steps are enough.

## Flow 2: Scripted Composition With Selected Return Data

1. Inspect the current runtime budgets:

```json
{
  "tool": "inspect_runtime_budgets",
  "arguments": {}
}
```

2. Run a scripted program and ask for only the artifacts you want back:

```json
{
  "tool": "run_tool_program",
  "arguments": {
    "program": "status = call_tool(\"fake_stdio.fake_status\")\nsummary = {\"status\": status.response.status, \"version\": status.response.version}",
    "result_variable": "summary",
    "return_variables": ["status"],
    "return_contract_summaries": ["fake_stdio.fake_status"]
  }
}
```

This keeps the response focused:

- `final_data` contains the selected result variable
- `variables` contains only the explicitly requested local variables
- `contract_summaries` contains only the explicitly requested mounted-tool summaries
- `initial_context` is not echoed back unless a specific variable derived from it is requested

## When To Use `return_contract_summaries`

Use `return_contract_summaries` when the host wants the program result plus the exact
tool summaries it used, for example to:

- show users which mounted tools contributed to a composed result
- cache a narrow contract view alongside the result
- feed a follow-up planning step without re-querying the full mounted inventory

## Batch vs Program

- Prefer `run_tool_batch` for short, straight-line orchestration.
- Prefer `run_tool_program` when you need loops, branching, or local reshaping.
- Ask for the minimum `return_variables` and `return_contract_summaries` you need.
- Re-check `inspect_runtime_budgets` before generating larger scripted programs.

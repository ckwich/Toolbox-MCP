# Toolbox Quickstart

This is the shortest path to exercise Toolbox locally with the seeded `fake_stdio`
managed toolset.

## 1. Install Dependencies

```bash
python -m pip install -e ".[dev]"
```

## 2. Run Verification Once

```bash
python -m pytest -q
```

On Windows, also run:

```powershell
python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning
```

## 3. Start Toolbox

```bash
python -m toolbox.server
```

Toolbox seeds a fake managed manifest in `.toolbox/fake_toolset_manifest.json` and
stores local state in `.toolbox/state.json`.

## 4. Walk The Intended Host Flow

Discover the seeded toolset:

```json
{
  "tool": "search_toolsets",
  "arguments": {
    "query": "fake status"
  }
}
```

Activate it:

```json
{
  "tool": "activate_toolsets",
  "arguments": {
    "namespaces": ["fake_stdio"],
    "scope": "thread"
  }
}
```

Describe the mounted tools cheaply:

```json
{
  "tool": "describe_mounted_tools",
  "arguments": {
    "namespaces": ["fake_stdio"]
  }
}
```

Run a constrained program against the mounted tool:

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

Deactivate it when you are done:

```json
{
  "tool": "deactivate_toolsets",
  "arguments": {
    "namespaces": ["fake_stdio"],
    "scope": "thread"
  }
}
```

## 5. Know Where To Read Next

- For broader host-facing composition patterns, see [composition-workflows.md](composition-workflows.md).
- For state and compatibility notes, see [README.md](../README.md).
- For the current operator/developer handoff, see [HANDOFF.md](../HANDOFF.md).

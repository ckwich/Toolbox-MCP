# Skill Loader MCP

Toolbox includes a small read-only MCP server for Codex skills. It is intentionally
separate from the Toolbox control plane so it can be loaded directly by Codex or mounted
through Toolbox as a managed stdio toolset.

The server reads `SKILL.md` files from configured roots and exposes four tools:

- `skills_list`: list skill metadata without loading full bodies
- `skills_load`: load one `SKILL.md` by frontmatter name or folder name
- `skills_search`: search names, descriptions, and skill bodies
- `skills_validate`: report missing required frontmatter fields

It does not execute skill scripts, follow arbitrary paths, or write skill files.

## Roots

The default roots are:

- `CODEX_SKILLS_ROOT`, or the user's `.codex/skills` directory
- `CODEX_PLUGIN_CACHE`, or the user's `.codex/plugins/cache` directory

User skills are included by default. System, primary-runtime, and plugin skills are
available only when the caller sets the matching include flag.

## Direct Codex Registration

Add this to your Codex `config.toml` when you want Codex to load the skill server
directly. Use paths for the host where Codex is running:

```toml
[mcp_servers.codexSkills]
command = "python"
args = ["-m", "toolbox.skills_server"]
cwd = "/path/to/Toolbox-MCP"
enabled = true

[mcp_servers.codexSkills.env]
PYTHONPATH = "/path/to/Toolbox-MCP"
CODEX_SKILLS_ROOT = "/path/to/.codex/skills"
CODEX_PLUGIN_CACHE = "/path/to/.codex/plugins/cache"
```

Restart the Codex client after changing `config.toml`.

## Catalog Pack Registration (Recommended)

If Toolbox is already loaded, prefer the repo-tracked catalog pack. It gives the
skill loader richer agent-facing metadata, lazy guidance, and an explicit composition
example without hand-writing a large `register_toolset` call.

```json
{
  "path": "docs/catalog-packs/codex-skills.json"
}
```

Recommended Toolbox flow:

1. `validate_catalog_pack(path="docs/catalog-packs/codex-skills.json")`
2. `import_catalog_pack(path="docs/catalog-packs/codex-skills.json", dry_run=true)`
3. `import_catalog_pack(path="docs/catalog-packs/codex-skills.json")`
4. `check_toolset_readiness(namespaces=["codex_skills"], probe=true, refresh_cache=true)`
5. `suggest_toolsets_for_task`, `plan_toolset_activation`, then `activate_toolsets` when a task needs skill loading

The pack uses a repo-relative stdio transport:

```json
{
  "command": "python",
  "args": ["-m", "toolbox.skills_server"],
  "cwd": ".",
  "env": {"PYTHONPATH": "."}
}
```

That keeps the checked-in pack portable for this repo. If Toolbox is launched from a
different working directory, either launch it with `cwd` set to the Toolbox checkout in the
host MCP config or use the manual registration shape below with absolute paths.

## Manual Toolbox-Managed Registration

If Toolbox is already loaded, register the skill server as a managed toolset:

```json
{
  "namespace": "codex_skills",
  "title": "Codex Skill Loader",
  "description": "Read-only listing, loading, searching, and validation for Codex SKILL.md files.",
  "transport": {
    "kind": "stdio",
    "command": "python",
    "args": ["-m", "toolbox.skills_server"],
    "cwd": "/path/to/Toolbox-MCP",
    "env": {
      "PYTHONPATH": "/path/to/Toolbox-MCP",
      "CODEX_SKILLS_ROOT": "/path/to/.codex/skills",
      "CODEX_PLUGIN_CACHE": "/path/to/.codex/plugins/cache"
    }
  },
  "tags": ["codex", "skills"],
  "default_scope": "thread",
  "supported_scopes": ["thread", "session"],
  "restorable_scopes": [],
  "restore_requires_identity": true,
  "restore_requires_explicit_request": true
}
```

Then activate it:

```json
{
  "namespaces": ["codex_skills"],
  "scope": "thread"
}
```

Mounted tool names will be `codex_skills.skills_list`,
`codex_skills.skills_load`, `codex_skills.skills_search`, and
`codex_skills.skills_validate`.

## Local Smoke

```bash
python -m pytest tests/test_skills_loader.py tests/test_skills_server.py tests/test_skills_server_entrypoint.py -q
python -m pytest tests/test_catalog_packs.py -q
python -m toolbox.skills_server
```

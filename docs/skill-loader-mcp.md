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

- `CODEX_SKILLS_ROOT`, or `%USERPROFILE%\.codex\skills`
- `CODEX_PLUGIN_CACHE`, or `%USERPROFILE%\.codex\plugins\cache`

User skills are included by default. System, primary-runtime, and plugin skills are
available only when the caller sets the matching include flag.

## Direct Codex Registration

Add this to `C:\Users\colek\.codex\config.toml` when you want Codex to load the skill
server directly:

```toml
[mcp_servers.codexSkills]
command = "python"
args = ["-m", "toolbox.skills_server"]
cwd = "C:\\Dev\\Toolbox"
enabled = true

[mcp_servers.codexSkills.env]
PYTHONPATH = "C:\\Dev\\Toolbox"
CODEX_SKILLS_ROOT = "C:\\Users\\colek\\.codex\\skills"
CODEX_PLUGIN_CACHE = "C:\\Users\\colek\\.codex\\plugins\\cache"
```

Restart the Codex client after changing `config.toml`.

## Toolbox-Managed Registration

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
    "cwd": "C:\\Dev\\Toolbox",
    "env": {
      "PYTHONPATH": "C:\\Dev\\Toolbox",
      "CODEX_SKILLS_ROOT": "C:\\Users\\colek\\.codex\\skills",
      "CODEX_PLUGIN_CACHE": "C:\\Users\\colek\\.codex\\plugins\\cache"
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

```powershell
python -m pytest tests\test_skills_loader.py tests\test_skills_server.py tests\test_skills_server_entrypoint.py -q
python -m toolbox.skills_server
```

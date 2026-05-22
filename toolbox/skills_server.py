from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from toolbox.skills_loader import SkillRegistry, default_plugin_cache, default_skills_root


def create_server(
    skills_root: Path | str | None = None,
    plugin_cache: Path | str | None = None,
) -> FastMCP:
    registry = SkillRegistry(
        skills_root=skills_root or default_skills_root(),
        plugin_cache=plugin_cache or default_plugin_cache(),
    )
    server = FastMCP(
        name="Codex Skill Loader",
        instructions=(
            "Read-only MCP server for listing, loading, searching, validating, and diagnosing "
            "Codex SKILL.md files from the configured Codex skills roots."
        ),
    )

    @server.tool(description="List installed Codex skills without loading full skill bodies.")
    def skills_list(
        query: str | None = None,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, object]:
        return registry.list_skills(
            query=query,
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )

    @server.tool(description="Load a single Codex SKILL.md by frontmatter name or folder name.")
    def skills_load(
        name: str,
        source: str | None = None,
        include_system: bool = True,
        include_runtime: bool = True,
        include_plugins: bool = False,
        max_chars: int = 20_000,
        section: str | None = None,
    ) -> dict[str, object]:
        return registry.load_skill(
            name,
            source=source,
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
            max_chars=max_chars,
            section=section,
        )

    @server.tool(description="Search installed Codex skills by name, description, and SKILL.md body text.")
    def skills_search(
        query: str,
        limit: int = 10,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, object]:
        return registry.search_skills(
            query,
            limit=limit,
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )

    @server.tool(description="Validate SKILL.md frontmatter and report missing required fields.")
    def skills_validate(
        name: str | None = None,
        source: str | None = None,
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, object]:
        return registry.validate_skills(
            name=name,
            source=source,
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )

    @server.tool(description="Report configured skill roots, source counts, and duplicate skill names.")
    def skills_roots_status(
        include_system: bool = False,
        include_runtime: bool = False,
        include_plugins: bool = False,
    ) -> dict[str, object]:
        return registry.roots_status(
            include_system=include_system,
            include_runtime=include_runtime,
            include_plugins=include_plugins,
        )

    return server


def main() -> None:
    try:
        create_server().run("stdio")
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()

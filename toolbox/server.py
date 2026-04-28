from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from toolbox.config import configured_state_path
from toolbox.models import BatchRunResult, BatchStep, ProgramRunResult
from toolbox.service import ToolboxService


class ToolboxServer(FastMCP):
    def __init__(self, service: ToolboxService):
        self.toolbox_service = service

        @asynccontextmanager
        async def lifespan(_: FastMCP):
            try:
                await self.toolbox_service.maybe_restore_on_startup()
                yield {}
            finally:
                await self.toolbox_service.shutdown()

        super().__init__(
            name="Toolbox",
            instructions=(
                "Toolbox is a control-plane MCP that lets hosts search, activate, refresh, "
                "inspect, and deactivate managed toolsets without eagerly loading all schemas. "
                "Call toolbox_brief first for low-token orientation, or suggest_toolsets_for_task "
                "when a task may need hidden capabilities."
            ),
            lifespan=lifespan,
        )

    async def list_tools(self) -> list[types.Tool]:
        static_tools = await super().list_tools()
        dynamic_tools = self.toolbox_service.list_mounted_tool_definitions()
        return [*static_tools, *dynamic_tools]

    async def call_tool(self, name: str, arguments: dict[str, object]) -> types.CallToolResult | list[types.ContentBlock] | dict[str, object]:
        static_tool = self._tool_manager.get_tool(name)
        if static_tool is not None:
            context = self.get_context()
            return await self._tool_manager.call_tool(name, arguments, context=context, convert_result=True)

        if self.toolbox_service.is_mounted_tool(name):
            return await self.toolbox_service.call_mounted_tool(name, arguments)

        raise ToolError(f"Unknown tool: {name}")


async def _notify_tool_list_changed(ctx: Context | None, changed: bool) -> None:
    if not changed or ctx is None:
        return
    await ctx.session.send_tool_list_changed()


def create_server(
    state_path: Path | None = None,
    *,
    restore_on_startup: bool = False,
    stable_host_identity: bool = False,
) -> FastMCP:
    service = ToolboxService(
        state_path=state_path or configured_state_path(),
        restore_on_startup=restore_on_startup,
        stable_host_identity=stable_host_identity,
    )
    server = ToolboxServer(service)

    @server.tool(
        description=(
            "Get a compact orientation to deferred Toolbox capabilities by category. "
            "Use this when you need to know what hidden toolsets may be available without activating them."
        )
    )
    def toolbox_overview(max_toolsets_per_category: int = 5) -> dict[str, object]:
        return service.toolbox_overview(max_toolsets_per_category=max_toolsets_per_category)

    @server.tool(
        description=(
            "Get a compact agent-facing brief for using Toolbox: current active toolsets, "
            "task-specific suggestions, recommended flow, and next action hints."
        )
    )
    def toolbox_brief(
        task: str | None = None,
        max_suggestions: int = 3,
        max_active: int = 5,
    ) -> dict[str, object]:
        return service.toolbox_brief(
            task=task,
            max_suggestions=max_suggestions,
            max_active=max_active,
        )

    @server.tool(
        description=(
            "Search registered deferred toolsets by namespace, title, description, category, tags, aliases, examples, "
            "and activation hints without loading full downstream schemas."
        )
    )
    def search_toolsets(query: str, limit: int = 10, include_inactive: bool = True) -> dict[str, object]:
        return service.search_toolsets(query=query, limit=limit, include_inactive=include_inactive)

    @server.tool(
        description=(
            "Suggest a short ranked list of deferred toolsets for the current task. "
            "Use this when a task may need capabilities that are not currently visible."
        )
    )
    def suggest_toolsets_for_task(
        task: str,
        limit: int = 5,
        include_inactive: bool = True,
    ) -> dict[str, object]:
        return service.suggest_toolsets_for_task(
            task=task,
            limit=limit,
            include_inactive=include_inactive,
        )

    @server.tool(
        description=(
            "Create a dry-run activation plan for a task before mounting deferred toolsets. "
            "Use this to keep activation deliberate, scoped, and explainable."
        )
    )
    def plan_toolset_activation(
        task: str,
        limit: int = 3,
        scope: str = "thread",
        include_inactive: bool = True,
    ) -> dict[str, object]:
        return service.plan_toolset_activation(
            task=task,
            limit=limit,
            scope=scope,
            include_inactive=include_inactive,
        )

    @server.tool(
        description=(
            "Load compact usage guidance, recipes, examples, and next actions for one registered toolset "
            "without mounting it."
        )
    )
    def get_toolset_guide(namespace: str) -> dict[str, object]:
        return service.get_toolset_guide(namespace=namespace)

    @server.tool(
        description=(
            "Audit registered toolset metadata for missing agent-facing guidance fields. "
            "Use this when improving the catalog itself."
        )
    )
    def audit_toolbox_catalog() -> dict[str, object]:
        return service.audit_toolbox_catalog()

    @server.tool(
        description=(
            "Inspect compact quality intelligence for registered toolsets: derived capability flags, "
            "quality grade, gaps, and recommended remediation without activating downstream servers."
        )
    )
    def inspect_toolset_quality(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.inspect_toolset_quality(namespaces=namespaces)

    @server.tool(
        description=(
            "List registered composition examples for one toolset without loading their full payloads."
        )
    )
    def list_toolset_examples(namespace: str) -> dict[str, object]:
        return service.list_toolset_examples(namespace=namespace)

    @server.tool(
        description=(
            "Load one explicit composition example payload by id after selecting a toolset."
        )
    )
    def load_toolset_example(namespace: str, example_id: str) -> dict[str, object]:
        return service.load_toolset_example(namespace=namespace, example_id=example_id)

    @server.tool(
        description=(
            "Load explicit guidance bodies for one registered toolset after selection. "
            "File-backed guidance is bounded to the toolset workspace and protected state paths are blocked."
        )
    )
    def load_toolset_guidance(namespace: str) -> dict[str, object]:
        return service.load_toolset_guidance(namespace=namespace)

    @server.tool(description="List registered toolsets and their current status.")
    def list_toolsets(scope: str | None = None) -> dict[str, object]:
        return service.list_toolsets(scope=scope)

    @server.tool(description="Get detailed status for one or more toolsets.")
    def get_toolset_status(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.get_toolset_status(namespaces=namespaces)

    @server.tool(description="Inspect cached tool contracts with compact schema summaries, without activating toolsets.")
    def inspect_cached_contracts(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.inspect_cached_contracts(namespaces=namespaces)

    @server.tool(description="Inspect mounted tool contracts for active toolsets with compact schema summaries.")
    def inspect_mounted_contracts(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.inspect_mounted_contracts(namespaces=namespaces)

    @server.tool(
        description=(
            "Describe currently mounted tools with compact composition-oriented summaries. "
            "Use this before writing a batch or program when you want the cheapest live tool view."
        )
    )
    def describe_mounted_tools(
        namespaces: list[str] | None = None,
        mounted_names: list[str] | None = None,
    ) -> dict[str, object]:
        return service.describe_mounted_tools(namespaces=namespaces, mounted_names=mounted_names)

    @server.tool(description="Diff cached tool contracts against the previous stored schema snapshot for one or more namespaces.")
    def diff_cached_contracts(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.diff_cached_contracts(namespaces=namespaces)

    @server.tool(description="Diff mounted tool contracts against the previous stored schema snapshot for active namespaces.")
    def diff_mounted_contracts(namespaces: list[str] | None = None) -> dict[str, object]:
        return service.diff_mounted_contracts(namespaces=namespaces)

    @server.tool(
        description=(
            "Inspect current program runtime budgets and transport timeout limits so composition callers "
            "can shape requests intentionally."
        )
    )
    def inspect_runtime_budgets() -> dict[str, object]:
        return service.inspect_runtime_budgets()

    @server.tool(description="List recent Toolbox lifecycle audit events with optional filters.")
    def list_audit_events(
        namespace: str | None = None,
        operation: str | None = None,
        outcome: str | None = None,
        registration_present: bool | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        return service.list_audit_events(
            namespace=namespace,
            operation=operation,
            outcome=outcome,
            registration_present=registration_present,
            limit=limit,
        )

    @server.tool(description="Run bounded health checks against active managed runtimes.")
    async def check_toolset_health(
        namespaces: list[str] | None = None,
        scope: str | None = None,
    ) -> dict[str, object]:
        return await service.check_toolset_health(namespaces=namespaces, scope=scope)

    @server.tool(description="Restore previously active restorable toolsets when policy and identity allow it.")
    async def restore_toolsets(
        namespaces: list[str] | None = None,
        stable_host_identity: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        result = await service.restore_toolsets(
            namespaces=namespaces,
            stable_host_identity=stable_host_identity,
            startup=False,
        )
        await _notify_tool_list_changed(ctx, bool(result["restored"]))
        return result

    @server.tool(description="Clear stale state and health-failure markers without reconnecting toolsets.")
    async def clear_stale_toolsets(namespaces: list[str] | None = None) -> dict[str, object]:
        return await service.clear_stale_toolsets(namespaces=namespaces)

    @server.tool(description="Register a managed toolset without activating it.")
    async def register_toolset(
        namespace: str,
        title: str,
        description: str,
        transport: dict[str, object],
        tags: list[str] | None = None,
        category: str = "general",
        aliases: list[str] | None = None,
        examples: list[str] | None = None,
        recipes: list[str] | None = None,
        activation_hint: str | None = None,
        cost_hint: str | None = None,
        latency_hint: str | None = None,
        trust_hint: str = "unknown",
        future_capabilities: dict[str, object] | None = None,
        guidance_sources: list[dict[str, object]] | None = None,
        composition_examples: list[dict[str, object]] | None = None,
        default_scope: str = "thread",
        supported_scopes: list[str] | None = None,
        restorable_scopes: list[str] | None = None,
        restore_requires_identity: bool = True,
        restore_requires_explicit_request: bool = True,
        auth_required: bool = False,
    ) -> dict[str, object]:
        return await service.register_toolset(
            namespace=namespace,
            title=title,
            description=description,
            transport=transport,
            tags=tags,
            category=category,
            aliases=aliases,
            examples=examples,
            recipes=recipes,
            activation_hint=activation_hint,
            cost_hint=cost_hint,
            latency_hint=latency_hint,
            trust_hint=trust_hint,
            future_capabilities=future_capabilities,
            guidance_sources=guidance_sources,
            composition_examples=composition_examples,
            default_scope=default_scope,
            supported_scopes=supported_scopes,
            restorable_scopes=restorable_scopes,
            restore_requires_identity=restore_requires_identity,
            restore_requires_explicit_request=restore_requires_explicit_request,
            auth_required=auth_required,
        )

    @server.tool(description="Activate one or more toolsets for a given scope.")
    async def activate_toolsets(
        namespaces: list[str],
        scope: str = "thread",
        if_not_loaded: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        result = await service.activate_toolsets(namespaces=namespaces, scope=scope, if_not_loaded=if_not_loaded)
        await _notify_tool_list_changed(ctx, bool(result["activated"]))
        return result

    @server.tool(description="Refresh one or more toolsets and atomically swap their cached contracts.")
    async def refresh_toolsets(
        namespaces: list[str] | None = None,
        if_stale: bool = True,
        scope: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        result = await service.refresh_toolsets(namespaces=namespaces, if_stale=if_stale, scope=scope)
        await _notify_tool_list_changed(ctx, bool(result["refreshed"]))
        return result

    @server.tool(description="Deactivate one or more toolsets from a given scope.")
    async def deactivate_toolsets(
        namespaces: list[str],
        scope: str = "thread",
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        result = await service.deactivate_toolsets(namespaces=namespaces, scope=scope, force=force)
        await _notify_tool_list_changed(ctx, bool(result["deactivated"]))
        return result

    @server.tool(description="Remove one or more registered toolsets from Toolbox.")
    async def unregister_toolsets(
        namespaces: list[str],
        force: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, object]:
        result = await service.unregister_toolsets(namespaces=namespaces, force=force)
        changed = bool(result["unregistered"]) or any(
            item.get("reason") == "disconnected_before_unregistration" for item in result["skipped"]
        )
        await _notify_tool_list_changed(ctx, changed)
        return result

    @server.tool(
        description=(
            "Compose multiple mounted tool calls in one round trip. "
            "Use {'$from': 'step_id.path.to.value'} inside later arguments to reference earlier step results."
        )
    )
    async def run_tool_batch(
        steps: list[BatchStep],
        final_step: str | None = None,
        continue_on_error: bool = False,
    ) -> BatchRunResult:
        return await service.run_tool_batch(
            steps=steps,
            final_step=final_step,
            continue_on_error=continue_on_error,
        )

    @server.tool(
        description=(
            "Execute a constrained Python-like tool composition program over mounted tools. "
            "Use call_tool('namespace.tool', arguments) and assign the final value to 'result' or another named result variable. "
            "Program variables and mounted-tool contract summaries are only returned when explicitly requested."
        )
    )
    async def run_tool_program(
        program: str,
        initial_context: dict[str, object] | None = None,
        result_variable: str = "result",
        return_variables: list[str] | None = None,
        return_contract_summaries: list[str] | None = None,
    ) -> ProgramRunResult:
        return await service.run_tool_program(
            program,
            initial_context=initial_context,
            result_variable=result_variable,
            return_variables=return_variables,
            return_contract_summaries=return_contract_summaries,
        )

    return server


def main() -> None:
    try:
        create_server().run("stdio")
    except KeyboardInterrupt:
        # Running a stdio MCP server directly in a terminal is a normal local
        # smoke path. Exit quietly when the user stops it instead of dumping the
        # AnyIO/MCP cancellation traceback.
        return


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import gc
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from toolbox.models import AuditOperation, AuditOutcome, BatchStep, HealthStatus, Scope
from toolbox.program_runtime import ToolProgramRuntime
from toolbox.registry import JsonStateStore
from toolbox.service import ToolboxService
from toolbox.server import create_server


def write_manifest(path: Path, tools: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tools": tools}, indent=2, sort_keys=True), encoding="utf-8")


def create_service(
    tmp_path: Path,
    *,
    restore_on_startup: bool = False,
    stable_host_identity: bool = False,
) -> ToolboxService:
    workspace = tmp_path
    state_path = workspace / ".toolbox" / "state.json"
    service = ToolboxService(
        state_path=state_path,
        restore_on_startup=restore_on_startup,
        stable_host_identity=stable_host_identity,
    )

    manifest_path = workspace / ".toolbox" / "fake_toolset_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(workspace)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)
    service.fake_manifest_path = manifest_path
    return service


async def shutdown_server(server) -> None:
    await server.toolbox_service.shutdown()
    if sys.platform != "win32":
        return

    for _ in range(3):
        await asyncio.sleep(0)

    gc.collect()

    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_activate_fake_toolset_persists_schema_and_scope(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = await service.activate_toolsets(["fake_stdio"], scope="thread")

        assert result["failed"] == []
        assert result["activated"][0]["namespace"] == "fake_stdio"

        status = service.get_toolset_status(["fake_stdio"])
        assert status["toolsets"][0]["activation"]["loaded_scopes"] == ["thread"]
        assert status["toolsets"][0]["schema"]["tool_count"] == 1
        assert status["toolsets"][0]["schema"]["schema_hash"].startswith("sha256:")
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_scope_policy_is_visible_in_list_and_status(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        listing = service.list_toolsets()
        fake_toolset = next(item for item in listing["toolsets"] if item["namespace"] == "fake_stdio")

        assert fake_toolset["scope_policy"] == {
            "default_scope": "thread",
            "loaded_scopes": [],
            "supported_scopes": ["thread", "session", "global"],
            "restorable_scopes": [],
            "recoverable_scopes": [],
            "non_restorable_scopes": ["thread", "session", "global"],
            "restore_requires_identity": True,
            "restore_requires_explicit_request": True,
        }

        status = service.get_toolset_status(["fake_stdio"])
        assert status["toolsets"][0]["registration"]["scope_policy"] == fake_toolset["scope_policy"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_inspect_cached_contracts_returns_summary_without_reactivation(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.deactivate_toolsets(["fake_stdio"], scope="thread")
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    recreated.fake_manifest_path = tmp_path / ".toolbox" / "fake_toolset_manifest.json"
    try:
        result = recreated.inspect_cached_contracts(["fake_stdio"])

        assert result["error"] is None
        assert result["count"] == 1

        contract = result["contracts"][0]
        assert contract["namespace"] == "fake_stdio"
        assert contract["source"] == "cached"
        assert contract["availability"] == {
            "registered": True,
            "cached": True,
            "mounted": False,
            "stale": False,
            "missing": False,
        }
        assert contract["tool_count"] == 1
        assert contract["schema_hash"].startswith("sha256:")
        assert contract["tools"][0]["name"] == "fake_status"
        assert contract["tools"][0]["mounted_name"] is None
        assert contract["tools"][0]["input_schema"]["root_type"] == "object"
        assert contract["tools"][0]["output_schema"]["root_type"] == "object"
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_inspect_cached_contracts_marks_missing_for_uncached_registered_toolset(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "uncached_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "uncached_status",
                "title": "Uncached Status",
                "description": "Return uncached fake status.",
                "response": {"status": "uncached"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="uncached_stdio",
            title="Uncached Managed Toolset",
            description="A registered but not yet activated toolset.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
        )
        assert registration["error"] is None

        result = service.inspect_cached_contracts(["uncached_stdio"])
        assert result["error"] is None
        assert result["count"] == 1

        contract = result["contracts"][0]
        assert contract["availability"] == {
            "registered": True,
            "cached": False,
            "mounted": False,
            "stale": False,
            "missing": True,
        }
        assert contract["tools"] == []
        assert contract["schema_hash"] is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_inspect_mounted_contracts_returns_live_summary_and_stale_state(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.mark_toolset_stale("fake_stdio", "manifest_changed")

        result = service.inspect_mounted_contracts(["fake_stdio"])

        assert result["error"] is None
        assert result["count"] == 1

        contract = result["contracts"][0]
        assert contract["namespace"] == "fake_stdio"
        assert contract["source"] == "mounted"
        assert contract["availability"] == {
            "registered": True,
            "cached": True,
            "mounted": True,
            "stale": True,
            "missing": False,
        }
        assert contract["tool_count"] == 1
        assert contract["tools"][0]["mounted_name"] == "fake_stdio.fake_status"
        assert contract["tools"][0]["tool_hash"].startswith("sha256:")
        assert contract["tools"][0]["input_schema"]["root_type"] == "object"
        assert contract["tools"][0]["output_schema"]["root_type"] == "object"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_describe_mounted_tools_returns_compact_summaries_and_missing_filters(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "not_mounted_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "not_mounted_status",
                "title": "Not Mounted Status",
                "description": "Return inactive fake status.",
                "response": {"status": "inactive"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="not_mounted_stdio",
            title="Not Mounted Toolset",
            description="A registered but inactive fake toolset.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
        )
        assert registration["error"] is None

        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = service.describe_mounted_tools(
            namespaces=["fake_stdio", "not_mounted_stdio", "unknown_stdio"],
            mounted_names=[
                "fake_stdio.fake_status",
                "fake_stdio.fake_missing",
                "ghost_stdio.fake_status",
            ],
        )

        assert result["error"] is None
        assert result["count"] == 1

        description = result["descriptions"][0]
        assert description["namespace"] == "fake_stdio"
        assert description["mounted_name"] == "fake_stdio.fake_status"
        assert description["source_name"] == "fake_status"
        assert description["tool_hash"].startswith("sha256:")
        assert description["required_inputs"] == []
        assert description["input_schema"]["root_type"] == "object"
        assert description["output_schema"]["root_type"] == "object"

        assert {"namespace": "not_mounted_stdio", "reason": "not_mounted"} in result["missing"]
        assert {"namespace": "unknown_stdio", "reason": "unknown_toolset"} in result["missing"]
        assert {"mounted_name": "fake_stdio.fake_missing", "reason": "tool_not_mounted"} in result["missing"]
        assert {"mounted_name": "ghost_stdio.fake_status", "reason": "unknown_toolset"} in result["missing"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_inspect_runtime_budgets_exposes_current_program_and_transport_limits(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = service.inspect_runtime_budgets()

        assert result["error"] is None
        assert result["mounted_tool_name_format"] == "namespace.tool"
        assert result["batch_reference_format"] == "step_id.path.to.value"
        assert result["program_return_variables_policy"] == "requested_only"
        assert result["default_result_variable"] == "result"
        assert result["program"] == {
            "max_program_length_chars": 12000,
            "max_ast_nodes": 500,
            "max_loop_iterations": 1000,
            "max_tool_calls": 128,
        }
        assert result["transport"] == {
            "bootstrap_timeout_seconds": 10.0,
            "mounted_tool_call_timeout_seconds": 30.0,
            "health_check_timeout_seconds": 5.0,
            "shutdown_timeout_seconds": 5.0,
        }
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_diff_cached_contracts_marks_previous_missing_for_first_snapshot(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")

        result = service.diff_cached_contracts(["fake_stdio"])

        assert result["error"] is None
        assert result["count"] == 1

        diff = result["diffs"][0]
        assert diff["namespace"] == "fake_stdio"
        assert diff["source"] == "cached"
        assert diff["availability"]["cached"] is True
        assert diff["previous_available"] is False
        assert diff["diff_available"] is False
        assert diff["current_schema_hash"].startswith("sha256:")
        assert diff["previous_schema_hash"] is None
        assert diff["changes"] == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_diff_cached_contracts_reports_compact_added_removed_and_changed_tools(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            },
            {
                "name": "fake_legacy",
                "title": "Fake Legacy",
                "description": "Return the legacy fake status.",
                "response": {"status": "legacy", "version": 1},
            },
        ],
    )

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.mark_toolset_stale("fake_stdio", "manifest_changed")
        write_manifest(
            manifest_path,
            [
                {
                    "name": "fake_status",
                    "title": "Fake Status",
                    "description": "Return the upgraded fake status.",
                    "response": {"status": "ok", "version": 2},
                },
                {
                    "name": "fake_metrics",
                    "title": "Fake Metrics",
                    "description": "Return fake metrics.",
                    "response": {"requests": 3},
                },
            ],
        )

        refresh = await service.refresh_toolsets(["fake_stdio"])
        assert refresh["failed"] == []

        result = service.diff_cached_contracts(["fake_stdio"])

        assert result["error"] is None
        assert result["count"] == 1

        diff = result["diffs"][0]
        assert diff["source"] == "cached"
        assert diff["previous_available"] is True
        assert diff["diff_available"] is True
        assert diff["has_changes"] is True
        assert diff["added_tools"] == ["fake_metrics"]
        assert diff["removed_tools"] == ["fake_legacy"]
        assert diff["changed_tools"] == ["fake_status"]
        assert diff["current_tool_count"] == 2
        assert diff["previous_tool_count"] == 2
        assert diff["current_schema_hash"].startswith("sha256:")
        assert diff["previous_schema_hash"].startswith("sha256:")

        changes_by_name = {item["name"]: item for item in diff["changes"]}
        assert changes_by_name["fake_metrics"]["change"] == "added"
        assert changes_by_name["fake_metrics"]["current_summary"]["name"] == "fake_metrics"
        assert changes_by_name["fake_metrics"]["current_summary"]["output_schema"]["root_type"] == "object"
        assert changes_by_name["fake_legacy"]["change"] == "removed"
        assert changes_by_name["fake_legacy"]["previous_summary"]["name"] == "fake_legacy"
        assert changes_by_name["fake_status"]["change"] == "changed"
        assert (
            changes_by_name["fake_status"]["previous_summary"]["tool_hash"]
            != changes_by_name["fake_status"]["current_summary"]["tool_hash"]
        )
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_diff_mounted_contracts_uses_live_runtime_against_previous_snapshot(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            },
            {
                "name": "fake_legacy",
                "title": "Fake Legacy",
                "description": "Return the legacy fake status.",
                "response": {"status": "legacy", "version": 1},
            },
        ],
    )

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.mark_toolset_stale("fake_stdio", "manifest_changed")
        write_manifest(
            manifest_path,
            [
                {
                    "name": "fake_status",
                    "title": "Fake Status",
                    "description": "Return the upgraded fake status.",
                    "response": {"status": "ok", "version": 2},
                },
                {
                    "name": "fake_metrics",
                    "title": "Fake Metrics",
                    "description": "Return fake metrics.",
                    "response": {"requests": 3},
                },
            ],
        )

        refresh = await service.refresh_toolsets(["fake_stdio"])
        assert refresh["failed"] == []

        result = service.diff_mounted_contracts(["fake_stdio"])

        assert result["error"] is None
        assert result["count"] == 1

        diff = result["diffs"][0]
        assert diff["source"] == "mounted"
        assert diff["availability"]["mounted"] is True
        assert diff["previous_available"] is True
        assert diff["diff_available"] is True
        assert diff["added_tools"] == ["fake_metrics"]
        assert diff["removed_tools"] == ["fake_legacy"]
        assert diff["changed_tools"] == ["fake_status"]

        changes_by_name = {item["name"]: item for item in diff["changes"]}
        assert changes_by_name["fake_metrics"]["mounted_name"] == "fake_stdio.fake_metrics"
        assert changes_by_name["fake_metrics"]["current_summary"]["mounted_name"] == "fake_stdio.fake_metrics"
        assert changes_by_name["fake_status"]["current_summary"]["mounted_name"] == "fake_stdio.fake_status"
        assert changes_by_name["fake_legacy"]["previous_summary"]["name"] == "fake_legacy"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_register_toolset_adds_new_registration_and_allows_activation(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "extra_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_extra_status",
                "title": "Fake Extra Status",
                "description": "Return extra fake status.",
                "response": {"status": "extra", "version": 7},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="extra_stdio",
            title="Extra Managed Toolset",
            description="A second fake managed toolset.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            tags=["extra", "test"],
        )

        assert registration["error"] is None
        assert registration["registered"]["namespace"] == "extra_stdio"

        status = service.get_toolset_status(["extra_stdio"])
        assert status["count"] == 1
        assert status["toolsets"][0]["activation"]["loaded_scopes"] == []

        activation = await service.activate_toolsets(["extra_stdio"], scope="thread")
        assert activation["failed"] == []

        result = await service.call_mounted_tool("extra_stdio.fake_extra_status", {})
        payload = parse_result_payload(result)
        assert payload["response"]["status"] == "extra"
        assert payload["response"]["version"] == 7
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_register_toolset_rejects_thread_as_restorable_scope(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = await service.register_toolset(
            namespace="bad_scope_stdio",
            title="Bad Scope Toolset",
            description="A toolset with an invalid restore policy.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
            },
            supported_scopes=["thread", "session"],
            restorable_scopes=["thread"],
        )

        assert result["registered"] is None
        assert result["error"]["code"] == "invalid_scope_policy"
        assert "thread scope cannot be marked restorable" in result["error"]["details"]["validation_error"]

        events = service.store.list_audit_events(
            namespace="bad_scope_stdio",
            operation=AuditOperation.REGISTER,
            outcome=AuditOutcome.FAILURE,
        )
        assert events[-1].error is not None
        assert events[-1].error.code == "invalid_scope_policy"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_register_toolset_persists_explicit_scope_policy_and_rejects_unsupported_activation(
    tmp_path: Path,
) -> None:
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "scoped_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "scoped_status",
                "title": "Scoped Status",
                "description": "Return scoped fake status.",
                "response": {"status": "scoped"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="scoped_stdio",
            title="Scoped Managed Toolset",
            description="A fake managed toolset with explicit scope policy.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["global", "session", "session"],
            restorable_scopes=["session", "session"],
        )

        assert registration["error"] is None
        assert registration["registered"]["scope_policy"] == {
            "default_scope": "session",
            "loaded_scopes": [],
            "supported_scopes": ["session", "global"],
            "restorable_scopes": ["session"],
            "recoverable_scopes": [],
            "non_restorable_scopes": ["global"],
            "restore_requires_identity": True,
            "restore_requires_explicit_request": True,
        }

        status = service.get_toolset_status(["scoped_stdio"])
        assert status["toolsets"][0]["registration"]["scope_policy"] == registration["registered"]["scope_policy"]

        blocked = await service.activate_toolsets(["scoped_stdio"], scope="thread")
        assert blocked["activated"] == []
        assert blocked["failed"][0]["code"] == "unsupported_scope"
        assert blocked["failed"][0]["details"]["supported_scopes"] == ["session", "global"]

        activated = await service.activate_toolsets(["scoped_stdio"], scope="session")
        assert activated["failed"] == []
        assert activated["activated"][0]["scope"] == "session"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_multi_scope_activation_reuses_runtime_and_partial_deactivation_preserves_mount(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        first = await service.activate_toolsets(["fake_stdio"], scope="thread")
        assert first["failed"] == []

        first_runtime = service.transport_manager.runtime_for("fake_stdio")
        assert first_runtime is not None
        first_status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]

        second = await service.activate_toolsets(["fake_stdio"], scope="session")
        assert second["failed"] == []

        second_runtime = service.transport_manager.runtime_for("fake_stdio")
        assert second_runtime is first_runtime
        assert second["activated"][0]["schema_hash"] == first["activated"][0]["schema_hash"]

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["loaded_scopes"] == ["thread", "session"]
        assert status["schema"]["previous_schema_hash"] == first_status["schema"]["previous_schema_hash"]

        partial = await service.deactivate_toolsets(["fake_stdio"], scope="thread")
        assert partial["failed"] == []
        assert partial["deactivated"][0]["remaining_scopes"] == ["session"]
        assert service.transport_manager.runtime_for("fake_stdio") is first_runtime

        mounted_result = await service.call_mounted_tool("fake_stdio.fake_status", {})
        mounted_payload = parse_result_payload(mounted_result)
        assert mounted_payload["response"]["status"] == "ok"

        after_partial = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert after_partial["activation"]["loaded_scopes"] == ["session"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_refresh_skips_inactive_toolset_to_avoid_implicit_activation(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.deactivate_toolsets(["fake_stdio"], scope="thread")
        await service.mark_toolset_stale("fake_stdio", "manifest_changed")

        refresh = await service.refresh_toolsets(["fake_stdio"])

        assert refresh["refreshed"] == []
        assert refresh["failed"] == []
        assert refresh["skipped"] == [{"namespace": "fake_stdio", "reason": "not_active"}]
        assert service.list_mounted_tools("fake_stdio") == []

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["loaded_scopes"] == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_check_toolset_health_reports_healthy_runtime_and_updates_status(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")

        result = await service.check_toolset_health(["fake_stdio"])

        assert result["error"] is None
        assert result["skipped"] == []
        assert result["failed"] == []
        assert result["checked"][0]["status"] == "healthy"
        assert result["checked"][0]["transport_state"] == "connected"
        assert result["checked"][0]["stale"] is False
        assert result["checked"][0]["cached_schema_hash"].startswith("sha256:")
        assert result["checked"][0]["observed_schema_hash"] == result["checked"][0]["cached_schema_hash"]

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["health"]["last_status"] == "healthy"
        assert status["health"]["last_observed_schema_hash"] == status["schema"]["schema_hash"]
        assert status["health"]["last_error"] is None

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.HEALTH_CHECK,
            outcome=AuditOutcome.SUCCESS,
        )
        assert events[-1].details["observed_schema_hash"] == status["schema"]["schema_hash"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_check_toolset_health_marks_toolset_stale_on_schema_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        snapshot = service.store.get_schema_snapshot("fake_stdio")
        assert snapshot is not None

        changed_tool = snapshot.tools[0].model_copy(update={"tool_hash": "sha256:changed-health-hash"})

        async def fake_probe_runtime(namespace: str) -> SimpleNamespace:
            assert namespace == "fake_stdio"
            return SimpleNamespace(normalized_tools=[changed_tool])

        monkeypatch.setattr(service.transport_manager, "probe_runtime", fake_probe_runtime)

        result = await service.check_toolset_health(["fake_stdio"])

        assert result["checked"][0]["status"] == "stale"
        assert result["checked"][0]["stale"] is True
        assert result["checked"][0]["stale_reason"] == "healthcheck_schema_changed"
        assert result["checked"][0]["error"]["code"] == "healthcheck_schema_changed"

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["stale"] is True
        assert status["activation"]["stale_reason"] == "healthcheck_schema_changed"
        assert status["health"]["last_status"] == "stale"
        assert status["health"]["last_error"]["code"] == "healthcheck_schema_changed"

        recent_failure = status["recent_failure"]
        assert recent_failure is not None
        assert recent_failure["latest_operation"] == "health_check"
        assert recent_failure["error"]["code"] == "healthcheck_schema_changed"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_check_toolset_health_marks_runtime_failed_and_unmounts_on_probe_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")

        async def fake_probe_runtime(namespace: str) -> SimpleNamespace:
            raise TimeoutError(f"probe timeout for {namespace}")

        monkeypatch.setattr(service.transport_manager, "probe_runtime", fake_probe_runtime)

        result = await service.check_toolset_health(["fake_stdio"])

        assert result["checked"][0]["status"] == "failed"
        assert result["checked"][0]["transport_state"] == "failed"
        assert result["checked"][0]["stale"] is True
        assert result["checked"][0]["stale_reason"] == "healthcheck_timeout"
        assert result["checked"][0]["error"]["code"] == "healthcheck_timeout"
        assert service.list_mounted_tools("fake_stdio") == []

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["transport_state"] == "failed"
        assert status["activation"]["stale"] is True
        assert status["activation"]["stale_reason"] == "healthcheck_timeout"
        assert status["health"]["last_status"] == "failed"
        assert status["health"]["last_error"]["code"] == "healthcheck_timeout"

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.HEALTH_CHECK,
            outcome=AuditOutcome.FAILURE,
        )
        assert events[-1].error is not None
        assert events[-1].error.code == "healthcheck_timeout"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_restore_toolsets_restores_recoverable_scopes_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "restore_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "restore_status",
                "title": "Restore Status",
                "description": "Return restore fake status.",
                "response": {"status": "restored"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="restore_stdio",
            title="Restore Managed Toolset",
            description="A toolset used to verify explicit restore.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["session", "global"],
            restorable_scopes=["session"],
            restore_requires_identity=False,
        )
        assert registration["error"] is None

        activation = await service.activate_toolsets(["restore_stdio"], scope="session")
        assert activation["failed"] == []
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    try:
        before = recreated.get_toolset_status(["restore_stdio"])["toolsets"][0]
        assert before["activation"]["loaded_scopes"] == []
        assert before["recovery"]["recoverable_scopes"] == ["session"]
        assert recreated.list_mounted_tools("restore_stdio") == []

        restored = await recreated.restore_toolsets(["restore_stdio"], stable_host_identity=False)

        assert restored["error"] is None
        assert restored["failed"] == []
        assert restored["skipped"] == []
        assert restored["restored"][0]["restored_scopes"] == ["session"]
        assert restored["restored"][0]["reused_runtime"] is False

        after = recreated.get_toolset_status(["restore_stdio"])["toolsets"][0]
        assert after["activation"]["loaded_scopes"] == ["session"]
        assert after["recovery"]["recoverable_scopes"] == ["session"]
        assert after["recovery"]["last_restored_at"] is not None

        mounted = await recreated.call_mounted_tool("restore_stdio.restore_status", {})
        payload = parse_result_payload(mounted)
        assert payload["response"]["status"] == "restored"
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_restore_toolsets_clears_prior_health_failure_metadata_on_success(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "restore_health_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "restore_health_status",
                "title": "Restore Health Status",
                "description": "Return restore-health fake status.",
                "response": {"status": "restored"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="restore_health_stdio",
            title="Restore Health Toolset",
            description="A toolset used to verify restore clears prior health state.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["session"],
            restorable_scopes=["session"],
            restore_requires_identity=False,
            restore_requires_explicit_request=False,
        )
        assert registration["error"] is None
        activation = await service.activate_toolsets(["restore_health_stdio"], scope="session")
        assert activation["failed"] == []

        record = service.store.get_toolset("restore_health_stdio")
        assert record is not None
        record.stale = True
        record.stale_reason = "healthcheck_timeout"
        record.last_health_status = HealthStatus.FAILED
        record.last_health_error = service._error_info(
            "healthcheck_timeout",
            "Probe timed out.",
            namespace="restore_health_stdio",
            retryable=True,
        )
        service.store.upsert_toolset(record)
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    try:
        restored = await recreated.restore_toolsets(["restore_health_stdio"], stable_host_identity=False)

        assert restored["error"] is None
        assert restored["failed"] == []
        assert restored["skipped"] == []
        assert restored["restored"][0]["restored_scopes"] == ["session"]

        status = recreated.get_toolset_status(["restore_health_stdio"])["toolsets"][0]
        assert status["activation"]["stale"] is False
        assert status["activation"]["stale_reason"] is None
        assert status["health"]["last_status"] is None
        assert status["health"]["last_error"] is None
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_restore_toolsets_respects_identity_requirement(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "identity_restore_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "identity_status",
                "title": "Identity Status",
                "description": "Return identity fake status.",
                "response": {"status": "identity"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="identity_restore_stdio",
            title="Identity Restore Toolset",
            description="A toolset used to verify identity-gated restore.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["session"],
            restorable_scopes=["session"],
            restore_requires_identity=True,
            restore_requires_explicit_request=False,
        )
        assert registration["error"] is None
        activation = await service.activate_toolsets(["identity_restore_stdio"], scope="session")
        assert activation["failed"] == []
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    try:
        blocked = await recreated.restore_toolsets(["identity_restore_stdio"], stable_host_identity=False)
        assert blocked["restored"] == []
        assert blocked["failed"] == []
        assert blocked["skipped"] == [
            {
                "namespace": "identity_restore_stdio",
                "reason": "identity_required",
                "recoverable_scopes": ["session"],
            }
        ]

        restored = await recreated.restore_toolsets(["identity_restore_stdio"], stable_host_identity=True)
        assert restored["failed"] == []
        assert restored["restored"][0]["restored_scopes"] == ["session"]
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_clear_stale_toolsets_resets_stale_flags_without_reconnecting(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        assert await service.mark_toolset_stale("fake_stdio", "manifest_changed") is True

        cleared = await service.clear_stale_toolsets(["fake_stdio"])

        assert cleared["error"] is None
        assert cleared["failed"] == []
        assert cleared["skipped"] == []
        assert cleared["cleared"][0]["namespace"] == "fake_stdio"
        assert cleared["cleared"][0]["cleared_stale_reason"] == "manifest_changed"
        assert cleared["cleared"][0]["transport_state"] == "inactive"

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["stale"] is False
        assert status["activation"]["stale_reason"] is None
        assert status["activation"]["transport_state"] == "inactive"
        assert status["health"]["last_status"] is None

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.CLEAR_STALE,
            outcome=AuditOutcome.SUCCESS,
        )
        assert events[-1].details["cleared_stale_reason"] == "manifest_changed"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stale_helpers_wait_for_namespace_lock(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    lock = service._namespace_lock("fake_stdio")

    try:
        await lock.acquire()

        mark_task = asyncio.create_task(service.mark_toolset_stale("fake_stdio", "manifest_changed"))
        await asyncio.sleep(0)
        assert mark_task.done() is False

        lock.release()
        assert await mark_task is True

        await lock.acquire()
        clear_task = asyncio.create_task(service.clear_stale_toolsets(["fake_stdio"]))
        await asyncio.sleep(0)
        assert clear_task.done() is False

        lock.release()
        cleared = await clear_task
        assert cleared["failed"] == []
        assert cleared["cleared"][0]["namespace"] == "fake_stdio"
    finally:
        if lock.locked():
            lock.release()
        await service.shutdown()


@pytest.mark.asyncio
async def test_startup_does_not_restore_loaded_scopes_without_restore_context(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "startup_scope_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "startup_scope_status",
                "title": "Startup Scope Status",
                "description": "Return scope restore status.",
                "response": {"status": "startup"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="startup_scope_stdio",
            title="Startup Scope Toolset",
            description="A toolset used to verify startup scope reconciliation.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["session", "global"],
            restorable_scopes=["session", "global"],
            restore_requires_identity=False,
            restore_requires_explicit_request=False,
        )
        assert registration["error"] is None

        session_activation = await service.activate_toolsets(["startup_scope_stdio"], scope="session")
        global_activation = await service.activate_toolsets(["startup_scope_stdio"], scope="global")
        assert session_activation["failed"] == []
        assert global_activation["failed"] == []
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    recreated.fake_manifest_path = tmp_path / ".toolbox" / "fake_toolset_manifest.json"
    try:
        status = recreated.get_toolset_status(["startup_scope_stdio"])
        assert status["count"] == 1
        assert status["toolsets"][0]["activation"]["loaded_scopes"] == []
        assert status["toolsets"][0]["activation"]["transport_state"] == "inactive"
        assert status["toolsets"][0]["registration"]["scope_policy"]["restorable_scopes"] == ["session", "global"]
        assert status["toolsets"][0]["schema"]["tool_count"] == 1
        assert recreated.list_mounted_tools("startup_scope_stdio") == []
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_register_toolset_rejects_duplicate_namespace(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        duplicate = await service.register_toolset(
            namespace="fake_stdio",
            title="Duplicate Fake",
            description="Should fail.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
            },
        )

        assert duplicate["registered"] is None
        assert duplicate["error"]["code"] == "toolset_exists"
        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.REGISTER,
            outcome=AuditOutcome.FAILURE,
        )
        assert events[-1].error is not None
        assert events[-1].error.code == "toolset_exists"
        assert events[-1].registration_present is True
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_unregister_toolset_rejects_active_without_force_and_removes_when_forced(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "force_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_force_status",
                "title": "Fake Force Status",
                "description": "Return force fake status.",
                "response": {"status": "force"},
            }
        ],
    )

    try:
        await service.register_toolset(
            namespace="force_stdio",
            title="Force Managed Toolset",
            description="A force-removable fake managed toolset.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
        )
        await service.activate_toolsets(["force_stdio"], scope="thread")

        blocked = await service.unregister_toolsets(["force_stdio"])
        assert blocked["unregistered"] == []
        assert blocked["failed"][0]["code"] == "toolset_active"

        removed = await service.unregister_toolsets(["force_stdio"], force=True)
        assert removed["failed"] == []
        assert removed["unregistered"] == [{"namespace": "force_stdio"}]
        assert service.get_toolset_status(["force_stdio"])["count"] == 0
        assert service.list_mounted_tools("force_stdio") == []

        events = service.store.list_audit_events(namespace="force_stdio")
        assert [event.operation.value for event in events] == [
            "register",
            "activate",
            "unregister",
            "unregister",
        ]
        assert [event.outcome.value for event in events] == [
            "success",
            "success",
            "failure",
            "success",
        ]
        assert events[2].error is not None
        assert events[2].error.code == "toolset_active"
        assert events[3].details["force"] is True
        assert events[3].details["disconnected_runtime"] is True
        assert all(event.registration_present is False for event in events)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_audit_history_persists_across_service_recreation_for_unregistered_toolset(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "audit_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "audit_status",
                "title": "Audit Status",
                "description": "Return audit fake status.",
                "response": {"status": "audit"},
            }
        ],
    )

    try:
        await service.register_toolset(
            namespace="audit_stdio",
            title="Audit Managed Toolset",
            description="A fake managed toolset for audit history checks.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
        )
        await service.activate_toolsets(["audit_stdio"], scope="thread")
        await service.deactivate_toolsets(["audit_stdio"], scope="thread")
        removed = await service.unregister_toolsets(["audit_stdio"])

        assert removed["failed"] == []

        events = service.store.list_audit_events(namespace="audit_stdio")
        assert [event.operation.value for event in events] == [
            "register",
            "activate",
            "deactivate",
            "unregister",
        ]
        assert [event.sequence for event in events] == sorted(event.sequence for event in events)
        assert all(event.outcome is AuditOutcome.SUCCESS for event in events)
        assert all(event.registration_present is False for event in events)
        assert service.store.get_toolset("audit_stdio") is None

        query = service.list_audit_events(namespace="audit_stdio", registration_present=False, limit=10)
        assert query["error"] is None
        assert query["count"] == 4
        assert [event["operation"] for event in query["events"]] == [
            "register",
            "activate",
            "deactivate",
            "unregister",
        ]

        unregister_query = service.list_audit_events(
            namespace="audit_stdio",
            operation="unregister",
            outcome="success",
            registration_present=False,
        )
        assert unregister_query["error"] is None
        assert unregister_query["count"] == 1
        assert unregister_query["events"][0]["operation"] == "unregister"
        assert unregister_query["events"][0]["registration_present"] is False
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    try:
        events = recreated.store.list_audit_events(namespace="audit_stdio")
        assert [event.operation.value for event in events] == [
            "register",
            "activate",
            "deactivate",
            "unregister",
        ]
        assert all(event.registration_present is False for event in events)
        assert recreated.store.get_toolset("audit_stdio") is None
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_list_audit_events_rejects_invalid_filters(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = service.list_audit_events(operation="bogus")
        assert result["count"] == 0
        assert result["events"] == []
        assert result["error"]["code"] == "invalid_audit_query"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_refresh_reports_added_removed_and_changed_tools(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        await service.mark_toolset_stale("fake_stdio", "manifest_changed")

        write_manifest(
            manifest_path,
            [
                {
                    "name": "fake_status",
                    "title": "Fake Status",
                    "description": "Return the upgraded fake status.",
                    "response": {"status": "ok", "version": 2},
                },
                {
                    "name": "fake_metrics",
                    "title": "Fake Metrics",
                    "description": "Return fake metrics.",
                    "response": {"requests": 3},
                },
            ],
        )

        result = await service.refresh_toolsets(["fake_stdio"])

        assert result["failed"] == []
        refresh = result["refreshed"][0]
        assert refresh["namespace"] == "fake_stdio"
        assert refresh["added_tools"] == ["fake_metrics"]
        assert refresh["removed_tools"] == []
        assert refresh["changed_tools"] == ["fake_status"]

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.REFRESH,
            outcome=AuditOutcome.SUCCESS,
        )
        assert events[-1].details["added_tools"] == ["fake_metrics"]
        assert events[-1].details["changed_tools"] == ["fake_status"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_failed_refresh_preserves_last_known_good_contract(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        before = service.get_toolset_status(["fake_stdio"])["toolsets"][0]["schema"]["schema_hash"]

        manifest_path.write_text("{ invalid json", encoding="utf-8")
        await service.mark_toolset_stale("fake_stdio", "manifest_broken")

        result = await service.refresh_toolsets(["fake_stdio"])

        assert result["refreshed"] == []
        assert result["failed"][0]["code"] == "refresh_failed"

        after_status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert after_status["schema"]["schema_hash"] == before
        assert after_status["activation"]["stale"] is True
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_deactivate_rejects_in_flight_calls_without_force(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope=Scope.THREAD.value)
        service.note_call_started("fake_stdio")
        result = await service.deactivate_toolsets(["fake_stdio"], scope=Scope.THREAD.value)

        assert result["deactivated"] == []
        assert result["failed"][0]["code"] == "in_flight_calls"

        service.note_call_finished("fake_stdio")
        forced = await service.deactivate_toolsets(["fake_stdio"], scope=Scope.THREAD.value)
        assert forced["failed"] == []
        assert forced["deactivated"][0]["namespace"] == "fake_stdio"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_batch_resolves_references_and_returns_final_data(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 2},
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = await service.run_tool_batch(
            steps=[
                BatchStep(id="status", tool="fake_stdio.fake_status"),
                BatchStep(
                    id="metrics",
                    tool="fake_stdio.fake_metrics",
                    arguments={
                        "arguments": {
                            "prior_status": {"$from": "status.response.status"},
                            "prior_version": {"$from": "status.response.version"},
                        }
                    },
                ),
            ],
            final_step="metrics",
        )

        assert result.stopped_on_error is False
        assert result.completed_steps == 2
        assert result.final_step == "metrics"
        assert result.final_data["arguments"]["prior_status"] == "ok"
        assert result.final_data["arguments"]["prior_version"] == 2
        assert result.final_data["response"]["requests"] == 3
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_executes_local_composition_logic(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 2},
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    program = """
status = call_tool("fake_stdio.fake_status")
if status.response.status == expected_status:
    metrics = call_tool(
        "fake_stdio.fake_metrics",
        {
            "arguments": {
                "prior_status": status.response.status,
                "prior_version": status.response.version,
            }
        },
    )
    result = {
        "status": status.response.status,
        "version": status.response.version,
        "requests": metrics.response.requests,
        "echo_status": metrics.arguments.prior_status,
    }
else:
    result = {"status": "unexpected"}
""".strip()

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = await service.run_tool_program(
            program,
            initial_context={"expected_status": "ok"},
            return_variables=["expected_status"],
        )

        assert result.call_count == 2
        assert result.final_data["status"] == "ok"
        assert result.final_data["version"] == 2
        assert result.final_data["requests"] == 3
        assert result.final_data["echo_status"] == "ok"
        assert result.variables["expected_status"] == "ok"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_supports_looped_composition_and_helpers(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_catalog",
                "title": "Fake Catalog",
                "description": "Return a list of fake status entries.",
                "response": {
                    "statuses": [
                        {"status": "beta", "version": 2},
                        {"status": "alpha", "version": 1},
                    ]
                },
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    program = """
catalog = call_tool("fake_stdio.fake_catalog")
versions = []
statuses = []
request_counts = []

for item in catalog.response.statuses:
    metric = call_tool(
        "fake_stdio.fake_metrics",
        {"arguments": {"status": item.status, "version": item.version}},
    )
    versions += [item.version]
    statuses += [metric.arguments.status]
    request_counts += [metric.response.requests]

summary_pairs = []
for key, value in items({"count": len(statuses), "request_total": sum(request_counts)}):
    summary_pairs += [[key, value]]

result = {
    "statuses": sorted(statuses),
    "version_total": sum(versions),
    "request_total": sum(request_counts),
    "ordinals": range(1, len(statuses) + 1),
    "summary_pairs": sorted(summary_pairs),
}
""".strip()

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = await service.run_tool_program(program)

        assert result.call_count == 3
        assert result.final_data["statuses"] == ["alpha", "beta"]
        assert result.final_data["version_total"] == 3
        assert result.final_data["request_total"] == 6
        assert result.final_data["ordinals"] == [1, 2]
        assert result.final_data["summary_pairs"] == [["count", 2], ["request_total", 6]]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_redacts_variables_by_default_and_supports_opt_in_return(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        redacted = await service.run_tool_program(
            "result = expected_status",
            initial_context={"expected_status": "ok", "secret_token": "hidden"},
        )
        assert redacted.success is True
        assert redacted.variables == {}

        selected = await service.run_tool_program(
            "result = expected_status",
            initial_context={"expected_status": "ok", "secret_token": "hidden"},
            return_variables=["expected_status", "secret_token"],
        )
        assert selected.variables == {"expected_status": "ok", "secret_token": "hidden"}
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_can_return_selected_contract_summaries_without_context_leak(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = await service.run_tool_program(
            """
status = call_tool("fake_stdio.fake_status")
observed_status = status.response.status
result = {"status": observed_status}
""".strip(),
            initial_context={"secret_token": "hidden"},
            return_variables=["observed_status"],
            return_contract_summaries=["fake_stdio.fake_status", "fake_stdio.fake_missing"],
        )

        assert result.success is True
        assert result.variables == {"observed_status": "ok"}
        assert result.contract_summaries[0].mounted_name == "fake_stdio.fake_status"
        assert result.contract_summaries[0].source_name == "fake_status"
        assert result.contract_summaries[0].input_schema.root_type == "object"
        assert result.missing_contract_summaries == [
            {"mounted_name": "fake_stdio.fake_missing", "reason": "tool_not_mounted"}
        ]

        payload = result.model_dump(mode="json")
        assert payload["variables"] == {"observed_status": "ok"}
        assert payload["contract_summaries"][0]["mounted_name"] == "fake_stdio.fake_status"
        assert "secret_token" not in json.dumps(payload)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_coerces_requested_opaque_variables_to_json_safe_values(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    class Opaque:
        pass

    try:
        result = await service.run_tool_program(
            "result = 1",
            initial_context={"opaque": Opaque()},
            return_variables=["opaque"],
        )

        assert result.success is True
        assert result.variables == {"opaque": {"type": "opaque", "class_name": "Opaque"}}
        assert result.model_dump(mode="json")["variables"]["opaque"]["class_name"] == "Opaque"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_coerces_opaque_final_data_to_json_safe_values(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    class Opaque:
        pass

    try:
        result = await service.run_tool_program(
            "result = opaque",
            initial_context={"opaque": Opaque()},
        )

        assert result.success is True
        assert result.final_data == {"type": "opaque", "class_name": "Opaque"}
        assert result.model_dump(mode="json")["final_data"]["class_name"] == "Opaque"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_redacts_call_trace_argument_values(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        activation = await service.activate_toolsets(["fake_stdio"], scope="thread")
        assert activation["failed"] == []

        result = await service.run_tool_program(
            'result = call_tool("fake_stdio.fake_status", {"token": secret_token, "nested": {"code": secret_token}})',
            initial_context={"secret_token": "super-secret-token"},
        )

        assert result.success is True
        assert result.calls[0].arguments == {
            "token": "[redacted]",
            "nested": {"code": "[redacted]"},
        }
        assert "super-secret-token" not in json.dumps(result.model_dump(mode="json"))
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_program_returns_structured_budget_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = create_service(tmp_path)
    program = """
total = 0
for value in range(5):
    total += value
result = total
""".strip()

    monkeypatch.setattr(ToolProgramRuntime, "MAX_LOOP_ITERATIONS", 2)

    try:
        result = await service.run_tool_program(program)

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "budget_exceeded"
        assert result.call_count == 0
        assert result.variables == {}
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_batch_returns_structured_failure_for_invalid_definition(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = await service.run_tool_batch(
            steps=[
                BatchStep(id="dup", tool="fake_stdio.fake_status"),
                BatchStep(id="dup", tool="fake_stdio.fake_status"),
            ]
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "invalid_batch"
        assert result.completed_steps == 0
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_batch_surfaces_first_step_failure_in_structured_error(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")
        result = await service.run_tool_batch(
            steps=[BatchStep(id="missing", tool="fake_stdio.fake_missing")],
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.code == "step_failed"
        assert result.results[0].is_error is True
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_run_tool_batch_redacts_resolved_argument_values(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        activation = await service.activate_toolsets(["fake_stdio"], scope="thread")
        assert activation["failed"] == []

        result = await service.run_tool_batch(
            steps=[
                BatchStep(
                    id="status",
                    tool="fake_stdio.fake_status",
                    arguments={"token": "super-secret-token", "nested": {"code": "super-secret-token"}},
                )
            ]
        )

        assert result.success is True
        assert result.results[0].resolved_arguments == {
            "token": "[redacted]",
            "nested": {"code": "[redacted]"},
        }
        assert "super-secret-token" not in json.dumps(result.model_dump(mode="json"))
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_register_and_status_redact_transport_env_values(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        result = await service.register_toolset(
            namespace="secret_stdio",
            title="Secret Toolset",
            description="A toolset used to verify transport env redaction.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--token", "super-secret-token"],
                "env": {"API_KEY": "super-secret-token", "VISIBLE": "ok"},
            },
        )

        expected_transport = {
            "kind": "stdio",
            "command": sys.executable,
            "cwd": None,
            "has_args": True,
            "arg_count": 4,
            "args_values_redacted": True,
            "has_env": True,
            "env_keys": ["API_KEY", "VISIBLE"],
            "env_values_redacted": True,
        }
        assert result["registered"]["transport"] == expected_transport
        assert "super-secret-token" not in json.dumps(result["registered"])

        status = service.get_toolset_status(["secret_stdio"])
        assert status["toolsets"][0]["registration"]["transport"] == expected_transport
        assert "super-secret-token" not in json.dumps(status["toolsets"][0]["registration"])
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_state_json_does_not_persist_transport_secret_values(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = create_service(tmp_path)
    manifest_path = tmp_path / ".toolbox" / "persisted_secret_manifest.json"
    secret = "state-super-secret-token"

    write_manifest(
        manifest_path,
        [
            {
                "name": "secret_status",
                "title": "Secret Status",
                "description": "Return fake status for encrypted persistence checks.",
                "response": {"status": "ok"},
            }
        ],
    )

    try:
        registration = await service.register_toolset(
            namespace="persisted_secret_stdio",
            title="Persisted Secret Toolset",
            description="A toolset used to verify transport secret persistence.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": [
                    "-m",
                    "toolbox.fake_managed_server",
                    "--manifest",
                    str(manifest_path),
                    "--token",
                    secret,
                ],
                "env": {"API_KEY": secret},
                "cwd": str(tmp_path),
            },
        )
        assert registration["error"] is None

        state_text = state_path.read_text(encoding="utf-8")
        assert secret not in state_text
    finally:
        await service.shutdown()

    recreated = ToolboxService(state_path=state_path)
    try:
        record = recreated.store.get_toolset("persisted_secret_stdio")
        assert record is not None
        assert record.transport.args[-1] == secret
        assert record.transport.env["API_KEY"] == secret
    finally:
        await recreated.shutdown()


@pytest.mark.asyncio
async def test_plaintext_transport_state_is_migrated_on_load(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    secret = "migrated-super-secret-token"
    manifest_path = tmp_path / ".toolbox" / "migrated_manifest.json"

    record = {
        "namespace": "migrated_secret_stdio",
        "title": "Migrated Secret Toolset",
        "description": "A plaintext transport record from an older state file.",
        "tags": [],
        "transport": {
            "kind": "stdio",
            "command": sys.executable,
            "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path), "--token", secret],
            "env": {"API_KEY": secret},
            "cwd": str(tmp_path),
        },
        "default_scope": "thread",
        "supported_scopes": ["thread", "session", "global"],
        "restorable_scopes": [],
        "recoverable_scopes": [],
        "restore_requires_identity": True,
        "restore_requires_explicit_request": True,
        "loaded_scopes": [],
        "transport_state": "inactive",
        "stale": False,
        "stale_reason": None,
        "schema_hash": None,
        "previous_schema_hash": None,
        "tool_count": 0,
        "last_activated_at": None,
        "last_refreshed_at": None,
        "last_known_good_at": None,
        "last_restored_at": None,
        "last_health_checked_at": None,
        "last_health_status": None,
        "last_health_observed_schema_hash": None,
        "last_health_observed_tool_count": None,
        "last_health_error": None,
        "last_error": None,
        "auth_required": False,
    }

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "toolsets": [record],
                "schema_cache": [],
                "previous_schema_cache": [],
                "audit_log": [],
                "next_audit_sequence": 1,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    store = JsonStateStore(state_path)
    records = store.list_toolsets()

    assert records[0].transport.args[-1] == secret
    assert records[0].transport.env["API_KEY"] == secret

    migrated_state_text = state_path.read_text(encoding="utf-8")
    assert secret not in migrated_state_text
    assert '"secret_envelope"' in migrated_state_text
    assert '"version": 2' in migrated_state_text


@pytest.mark.asyncio
async def test_activate_clears_failed_health_metadata(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        record = service.store.get_toolset("fake_stdio")
        assert record is not None
        record.stale = True
        record.stale_reason = "healthcheck_timeout"
        record.last_health_status = HealthStatus.FAILED
        record.last_health_checked_at = datetime.now(timezone.utc)
        record.last_health_observed_schema_hash = "sha256:stale"
        record.last_health_observed_tool_count = 9
        record.last_health_error = service._error_info(
            "healthcheck_timeout",
            "Probe timed out.",
            namespace="fake_stdio",
            retryable=True,
        )
        service.store.upsert_toolset(record)

        result = await service.activate_toolsets(["fake_stdio"], scope="thread")

        assert result["failed"] == []
        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["stale"] is False
        assert status["activation"]["stale_reason"] is None
        assert status["health"]["last_status"] is None
        assert status["health"]["last_checked_at"] is None
        assert status["health"]["last_observed_schema_hash"] is None
        assert status["health"]["last_observed_tool_count"] is None
        assert status["health"]["last_error"] is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_refresh_clears_failed_health_metadata(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        activation = await service.activate_toolsets(["fake_stdio"], scope="thread")
        assert activation["failed"] == []

        record = service.store.get_toolset("fake_stdio")
        assert record is not None
        record.stale = True
        record.stale_reason = "healthcheck_timeout"
        record.last_health_status = HealthStatus.FAILED
        record.last_health_checked_at = datetime.now(timezone.utc)
        record.last_health_observed_schema_hash = "sha256:stale"
        record.last_health_observed_tool_count = 9
        record.last_health_error = service._error_info(
            "healthcheck_timeout",
            "Probe timed out.",
            namespace="fake_stdio",
            retryable=True,
        )
        service.store.upsert_toolset(record)

        result = await service.refresh_toolsets(["fake_stdio"])

        assert result["failed"] == []
        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]
        assert status["activation"]["stale"] is False
        assert status["activation"]["stale_reason"] is None
        assert status["health"]["last_status"] is None
        assert status["health"]["last_checked_at"] is None
        assert status["health"]["last_observed_schema_hash"] is None
        assert status["health"]["last_observed_tool_count"] is None
        assert status["health"]["last_error"] is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_concurrent_activation_is_serialized_per_namespace(tmp_path: Path) -> None:
    service = create_service(tmp_path)

    try:
        first, second = await asyncio.gather(
            service.activate_toolsets(["fake_stdio"], scope="thread"),
            service.activate_toolsets(["fake_stdio"], scope="thread"),
        )

        activated_count = len(first["activated"]) + len(second["activated"])
        skipped_count = len(first["skipped"]) + len(second["skipped"])
        failed_count = len(first["failed"]) + len(second["failed"])

        assert activated_count == 1
        assert skipped_count == 1
        assert failed_count == 0
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_call_timeout_marks_runtime_failed_and_unmounts_tools(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path
    service.transport_manager.call_timeout_seconds = 0.05

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_slow",
                "title": "Fake Slow",
                "description": "Return after a delay.",
                "delay_seconds": 0.2,
                "response": {"status": "slow"},
            }
        ],
    )

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")

        with pytest.raises(TimeoutError):
            await service.call_mounted_tool("fake_stdio.fake_slow", {})

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]["activation"]
        assert status["transport_state"] == "failed"
        assert status["stale"] is True
        assert status["stale_reason"] == "call_timeout"
        assert service.list_mounted_tools("fake_stdio") == []

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.CALL,
            outcome=AuditOutcome.FAILURE,
        )
        assert events[-1].error is not None
        assert events[-1].error.code == "call_timeout"
        assert events[-1].details["mounted_tool"] == "fake_stdio.fake_slow"
        assert events[-1].registration_present is True

        status_summary = service.get_toolset_status(["fake_stdio"])["toolsets"][0]["recent_failure"]
        assert status_summary is not None
        assert status_summary["count"] >= 1
        assert status_summary["latest_operation"] == "call"
        assert status_summary["registration_present"] is True
        assert status_summary["error"]["code"] == "call_timeout"
        assert status_summary["details"]["mounted_tool"] == "fake_stdio.fake_slow"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_runtime_exit_fails_pending_calls_without_hanging(tmp_path: Path) -> None:
    service = create_service(tmp_path)
    manifest_path = service.fake_manifest_path
    service.transport_manager.call_timeout_seconds = 0.5

    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_crash",
                "title": "Fake Crash",
                "description": "Crash the managed process after a short delay.",
                "delay_seconds": 0.05,
                "crash_process": True,
                "response": {"status": "boom"},
            },
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok"},
            },
        ],
    )

    try:
        await service.activate_toolsets(["fake_stdio"], scope="thread")

        crash_task = asyncio.create_task(service.call_mounted_tool("fake_stdio.fake_crash", {}))
        await asyncio.sleep(0.01)
        queued_task = asyncio.create_task(service.call_mounted_tool("fake_stdio.fake_status", {}))

        results = await asyncio.wait_for(
            asyncio.gather(crash_task, queued_task, return_exceptions=True),
            timeout=1.0,
        )

        assert all(isinstance(item, Exception) for item in results)

        status = service.get_toolset_status(["fake_stdio"])["toolsets"][0]["activation"]
        assert status["transport_state"] == "failed"
        assert status["stale"] is True

        events = service.store.list_audit_events(
            namespace="fake_stdio",
            operation=AuditOperation.CALL,
            outcome=AuditOutcome.FAILURE,
        )
        assert len(events) >= 2
        assert all(event.error is not None for event in events[-2:])
        assert all(event.error.code == "transport_failed" for event in events[-2:])
    finally:
        await service.shutdown()


def parse_result_payload(result) -> dict:
    text_blocks = [block.text for block in result.content if hasattr(block, "text")]
    assert text_blocks
    return json.loads(text_blocks[0])


@pytest.mark.asyncio
async def test_server_mounts_live_tool_and_forwards_calls(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            before_tools = await client.list_tools()
            assert "fake_stdio.fake_status" not in {tool.name for tool in before_tools.tools}

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            after_tools = await client.list_tools()
            tool_names = {tool.name for tool in after_tools.tools}
            assert "fake_stdio.fake_status" in tool_names

            result = await client.call_tool("fake_stdio.fake_status", {})
            assert result.isError is False
            mounted_payload = parse_result_payload(result)
            assert mounted_payload["response"]["status"] == "ok"
            assert mounted_payload["response"]["version"] == 1

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []

            final_tools = await client.list_tools()
            assert "fake_stdio.fake_status" not in {tool.name for tool in final_tools.tools}
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_can_register_activate_and_unregister_toolset(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    manifest_path = tmp_path / ".toolbox" / "registered_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "registered_status",
                "title": "Registered Status",
                "description": "Return registered fake status.",
                "response": {"status": "registered", "version": 11},
            }
        ],
    )

    server = create_server(state_path=state_path)

    try:
        async with create_connected_server_and_client_session(server) as client:
            registration = await client.call_tool(
                "register_toolset",
                {
                    "namespace": "registered_stdio",
                    "title": "Registered Managed Toolset",
                    "description": "A dynamically registered fake toolset.",
                    "transport": {
                        "kind": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                        "cwd": str(tmp_path),
                    },
                    "tags": ["registered", "test"],
                },
            )
            registration_payload = parse_result_payload(registration)
            assert registration_payload["error"] is None
            assert registration_payload["registered"]["namespace"] == "registered_stdio"

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["registered_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            mounted = await client.call_tool("registered_stdio.registered_status", {})
            mounted_payload = parse_result_payload(mounted)
            assert mounted_payload["response"]["status"] == "registered"
            assert mounted_payload["response"]["version"] == 11

            unregistration = await client.call_tool("unregister_toolsets", {"namespaces": ["registered_stdio"], "force": True})
            unregistration_payload = parse_result_payload(unregistration)
            assert unregistration_payload["failed"] == []
            assert unregistration_payload["unregistered"] == [{"namespace": "registered_stdio"}]

            status = await client.call_tool("get_toolset_status", {"namespaces": ["registered_stdio"]})
            status_payload = parse_result_payload(status)
            assert status_payload["count"] == 0
            assert status_payload["missing"][0]["code"] == "unknown_toolset"
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_register_and_status_expose_scope_policy(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    manifest_path = tmp_path / ".toolbox" / "scope_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "scope_status",
                "title": "Scope Status",
                "description": "Return scope fake status.",
                "response": {"status": "scope"},
            }
        ],
    )

    server = create_server(state_path=state_path)

    try:
        async with create_connected_server_and_client_session(server) as client:
            registration = await client.call_tool(
                "register_toolset",
                {
                    "namespace": "scope_stdio",
                    "title": "Scope Managed Toolset",
                    "description": "A dynamically registered scoped toolset.",
                    "transport": {
                        "kind": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                        "cwd": str(tmp_path),
                    },
                    "default_scope": "session",
                    "supported_scopes": ["session", "global"],
                    "restorable_scopes": ["global"],
                    "restore_requires_identity": False,
                    "restore_requires_explicit_request": True,
                },
            )
            registration_payload = parse_result_payload(registration)
            assert registration_payload["error"] is None
            assert registration_payload["registered"]["scope_policy"] == {
                "default_scope": "session",
                "loaded_scopes": [],
                "supported_scopes": ["session", "global"],
                "restorable_scopes": ["global"],
                "recoverable_scopes": [],
                "non_restorable_scopes": ["session"],
                "restore_requires_identity": False,
                "restore_requires_explicit_request": True,
            }

            status = await client.call_tool("get_toolset_status", {"namespaces": ["scope_stdio"]})
            status_payload = parse_result_payload(status)
            assert status_payload["toolsets"][0]["registration"]["scope_policy"] == (
                registration_payload["registered"]["scope_policy"]
            )
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_scope_isolation_keeps_tool_mounted_until_last_scope_removed(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "session"})

            status = await client.call_tool("get_toolset_status", {"namespaces": ["fake_stdio"]})
            status_payload = parse_result_payload(status)
            assert status_payload["toolsets"][0]["activation"]["loaded_scopes"] == ["thread", "session"]

            partial = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            partial_payload = parse_result_payload(partial)
            assert partial_payload["failed"] == []
            assert partial_payload["deactivated"][0]["remaining_scopes"] == ["session"]

            mid_tools = await client.list_tools()
            assert "fake_stdio.fake_status" in {tool.name for tool in mid_tools.tools}

            mid_status = await client.call_tool("get_toolset_status", {"namespaces": ["fake_stdio"]})
            mid_status_payload = parse_result_payload(mid_status)
            assert mid_status_payload["toolsets"][0]["activation"]["loaded_scopes"] == ["session"]

            final = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "session"})
            final_payload = parse_result_payload(final)
            assert final_payload["failed"] == []

            final_tools = await client.list_tools()
            assert "fake_stdio.fake_status" not in {tool.name for tool in final_tools.tools}
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_check_toolset_health_exposes_runtime_probe_results(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            initial_tools = await client.list_tools()
            assert "check_toolset_health" in {tool.name for tool in initial_tools.tools}

            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            health = await client.call_tool("check_toolset_health", {"namespaces": ["fake_stdio"]})
            health_payload = parse_result_payload(health)
            assert health_payload["error"] is None
            assert health_payload["checked"][0]["status"] == "healthy"
            assert health_payload["checked"][0]["transport_state"] == "connected"

            status = await client.call_tool("get_toolset_status", {"namespaces": ["fake_stdio"]})
            status_payload = parse_result_payload(status)
            assert status_payload["toolsets"][0]["health"]["last_status"] == "healthy"
            assert status_payload["toolsets"][0]["health"]["last_error"] is None
    finally:
        await shutdown_server(server)
        del server


async def test_server_startup_restore_restores_configured_restorable_scopes(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    bootstrap = ToolboxService(state_path=state_path)
    manifest_path = tmp_path / ".toolbox" / "startup_restore_server_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "startup_restore_status",
                "title": "Startup Restore Status",
                "description": "Return startup restore fake status.",
                "response": {"status": "startup-restored"},
            }
        ],
    )

    try:
        registration = await bootstrap.register_toolset(
            namespace="startup_restore_stdio",
            title="Startup Restore Toolset",
            description="A toolset used to verify startup restore in server lifespan.",
            transport={
                "kind": "stdio",
                "command": sys.executable,
                "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                "cwd": str(tmp_path),
            },
            default_scope="session",
            supported_scopes=["session"],
            restorable_scopes=["session"],
            restore_requires_identity=False,
            restore_requires_explicit_request=False,
        )
        assert registration["error"] is None
        activation = await bootstrap.activate_toolsets(["startup_restore_stdio"], scope="session")
        assert activation["failed"] == []
    finally:
        await bootstrap.shutdown()

    server = create_server(
        state_path=state_path,
        restore_on_startup=True,
        stable_host_identity=False,
    )

    try:
        async with create_connected_server_and_client_session(server) as client:
            tools = await client.list_tools()
            assert "startup_restore_stdio.startup_restore_status" in {tool.name for tool in tools.tools}

            status = await client.call_tool("get_toolset_status", {"namespaces": ["startup_restore_stdio"]})
            status_payload = parse_result_payload(status)
            assert status_payload["toolsets"][0]["activation"]["loaded_scopes"] == ["session"]
            assert status_payload["toolsets"][0]["recovery"]["startup_restore_enabled"] is True
            assert status_payload["toolsets"][0]["recovery"]["recoverable_scopes"] == ["session"]

            mounted = await client.call_tool("startup_restore_stdio.startup_restore_status", {})
            mounted_payload = parse_result_payload(mounted)
            assert mounted_payload["response"]["status"] == "startup-restored"
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_inspection_surfaces_cached_and_mounted_contracts(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            initial_tools = await client.list_tools()
            initial_tool_names = {tool.name for tool in initial_tools.tools}
            assert "inspect_cached_contracts" in initial_tool_names
            assert "inspect_mounted_contracts" in initial_tool_names

            before_activation = await client.call_tool("inspect_cached_contracts", {"namespaces": ["fake_stdio"]})
            before_activation_payload = parse_result_payload(before_activation)
            assert before_activation_payload["error"] is None
            assert before_activation_payload["contracts"][0]["availability"]["missing"] is True
            assert before_activation_payload["contracts"][0]["availability"]["cached"] is False

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            mounted = await client.call_tool("inspect_mounted_contracts", {"namespaces": ["fake_stdio"]})
            mounted_payload = parse_result_payload(mounted)
            assert mounted_payload["error"] is None
            assert mounted_payload["contracts"][0]["availability"]["mounted"] is True
            assert mounted_payload["contracts"][0]["tools"][0]["mounted_name"] == "fake_stdio.fake_status"

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []

            cached = await client.call_tool("inspect_cached_contracts", {"namespaces": ["fake_stdio"]})
            cached_payload = parse_result_payload(cached)
            assert cached_payload["error"] is None
            assert cached_payload["contracts"][0]["availability"]["cached"] is True
            assert cached_payload["contracts"][0]["availability"]["mounted"] is False
            assert cached_payload["contracts"][0]["availability"]["missing"] is False
            assert cached_payload["contracts"][0]["tools"][0]["name"] == "fake_status"
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_diff_surfaces_report_cached_and_mounted_contract_changes(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            },
            {
                "name": "fake_legacy",
                "title": "Fake Legacy",
                "description": "Return the legacy fake status.",
                "response": {"status": "legacy", "version": 1},
            },
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            initial_tools = await client.list_tools()
            initial_tool_names = {tool.name for tool in initial_tools.tools}
            assert "diff_cached_contracts" in initial_tool_names
            assert "diff_mounted_contracts" in initial_tool_names

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            write_manifest(
                manifest_path,
                [
                    {
                        "name": "fake_status",
                        "title": "Fake Status",
                        "description": "Return the upgraded fake status.",
                        "response": {"status": "ok", "version": 2},
                    },
                    {
                        "name": "fake_metrics",
                        "title": "Fake Metrics",
                        "description": "Return fake metrics.",
                        "response": {"requests": 3},
                    },
                ],
            )

            await server.toolbox_service.mark_toolset_stale("fake_stdio", "manifest_changed")
            refresh = await client.call_tool("refresh_toolsets", {"namespaces": ["fake_stdio"]})
            refresh_payload = parse_result_payload(refresh)
            assert refresh_payload["failed"] == []

            cached_diff = await client.call_tool("diff_cached_contracts", {"namespaces": ["fake_stdio"]})
            cached_diff_payload = parse_result_payload(cached_diff)
            assert cached_diff_payload["error"] is None
            assert cached_diff_payload["diffs"][0]["added_tools"] == ["fake_metrics"]
            assert cached_diff_payload["diffs"][0]["removed_tools"] == ["fake_legacy"]
            assert cached_diff_payload["diffs"][0]["changed_tools"] == ["fake_status"]

            mounted_diff = await client.call_tool("diff_mounted_contracts", {"namespaces": ["fake_stdio"]})
            mounted_diff_payload = parse_result_payload(mounted_diff)
            assert mounted_diff_payload["error"] is None
            assert mounted_diff_payload["diffs"][0]["source"] == "mounted"
            assert mounted_diff_payload["diffs"][0]["diff_available"] is True
            changes_by_name = {item["name"]: item for item in mounted_diff_payload["diffs"][0]["changes"]}
            assert changes_by_name["fake_metrics"]["mounted_name"] == "fake_stdio.fake_metrics"
            assert changes_by_name["fake_status"]["current_summary"]["mounted_name"] == "fake_stdio.fake_status"
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_exposes_precomposition_description_and_runtime_budget_surfaces(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            initial_tools = await client.list_tools()
            initial_tool_names = {tool.name for tool in initial_tools.tools}
            assert "describe_mounted_tools" in initial_tool_names
            assert "inspect_runtime_budgets" in initial_tool_names

            budget = await client.call_tool("inspect_runtime_budgets", {})
            budget_payload = parse_result_payload(budget)
            assert budget_payload["error"] is None
            assert budget_payload["program"]["max_tool_calls"] == 128
            assert budget_payload["transport"]["mounted_tool_call_timeout_seconds"] == 30.0

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            describe = await client.call_tool(
                "describe_mounted_tools",
                {
                    "namespaces": ["fake_stdio"],
                    "mounted_names": ["fake_stdio.fake_status"],
                },
            )
            describe_payload = parse_result_payload(describe)
            assert describe_payload["error"] is None
            assert describe_payload["count"] == 1
            assert describe_payload["missing"] == []
            assert describe_payload["descriptions"][0]["mounted_name"] == "fake_stdio.fake_status"
            assert describe_payload["descriptions"][0]["source_name"] == "fake_status"
            assert describe_payload["descriptions"][0]["input_schema"]["root_type"] == "object"

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_list_audit_events_exposes_recent_history_for_unregistered_namespace(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    manifest_path = tmp_path / ".toolbox" / "audit_server_manifest.json"
    write_manifest(
        manifest_path,
        [
            {
                "name": "audit_status",
                "title": "Audit Status",
                "description": "Return audit fake status.",
                "response": {"status": "audit", "version": 5},
            }
        ],
    )

    server = create_server(state_path=state_path)

    try:
        async with create_connected_server_and_client_session(server) as client:
            initial_tools = await client.list_tools()
            initial_tool_names = {tool.name for tool in initial_tools.tools}
            assert "list_audit_events" in initial_tool_names

            registration = await client.call_tool(
                "register_toolset",
                {
                    "namespace": "audit_server_stdio",
                    "title": "Audit Server Toolset",
                    "description": "A fake managed toolset for audit query integration checks.",
                    "transport": {
                        "kind": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)],
                        "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                        "cwd": str(tmp_path),
                    },
                },
            )
            registration_payload = parse_result_payload(registration)
            assert registration_payload["error"] is None

            activation = await client.call_tool("activate_toolsets", {"namespaces": ["audit_server_stdio"], "scope": "thread"})
            activation_payload = parse_result_payload(activation)
            assert activation_payload["failed"] == []

            active_tools = await client.list_tools()
            active_tool_names = {tool.name for tool in active_tools.tools}
            assert "list_audit_events" in active_tool_names
            assert "audit_server_stdio.audit_status" in active_tool_names

            mounted = await client.call_tool("audit_server_stdio.audit_status", {})
            mounted_payload = parse_result_payload(mounted)
            assert mounted_payload["response"]["status"] == "audit"

            active_query = await client.call_tool(
                "list_audit_events",
                {
                    "namespace": "audit_server_stdio",
                    "registration_present": True,
                    "limit": 10,
                },
            )
            active_query_payload = parse_result_payload(active_query)
            assert active_query_payload["error"] is None
            assert active_query_payload["count"] == 2
            assert [event["operation"] for event in active_query_payload["events"]] == ["register", "activate"]

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["audit_server_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []

            unregistration = await client.call_tool("unregister_toolsets", {"namespaces": ["audit_server_stdio"]})
            unregistration_payload = parse_result_payload(unregistration)
            assert unregistration_payload["failed"] == []

            retained_query = await client.call_tool(
                "list_audit_events",
                {
                    "namespace": "audit_server_stdio",
                    "registration_present": False,
                    "limit": 10,
                },
            )
            retained_query_payload = parse_result_payload(retained_query)
            assert retained_query_payload["error"] is None
            assert retained_query_payload["count"] == 4
            assert [event["operation"] for event in retained_query_payload["events"]] == [
                "register",
                "activate",
                "deactivate",
                "unregister",
            ]
            assert all(event["registration_present"] is False for event in retained_query_payload["events"])

            final_tools = await client.list_tools()
            final_tool_names = {tool.name for tool in final_tools.tools}
            assert "list_audit_events" in final_tool_names
            assert "audit_server_stdio.audit_status" not in final_tool_names
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_refresh_swaps_mounted_tool_inventory(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 1},
            }
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            write_manifest(
                manifest_path,
                [
                    {
                        "name": "fake_status",
                        "title": "Fake Status",
                        "description": "Return the upgraded fake status.",
                        "response": {"status": "ok", "version": 2},
                    },
                    {
                        "name": "fake_metrics",
                        "title": "Fake Metrics",
                        "description": "Return fake metrics.",
                        "response": {"requests": 3},
                    },
                ],
            )

            await server.toolbox_service.mark_toolset_stale("fake_stdio", "manifest_changed")
            refresh = await client.call_tool("refresh_toolsets", {"namespaces": ["fake_stdio"]})
            refresh_payload = parse_result_payload(refresh)
            assert refresh_payload["failed"] == []
            assert refresh_payload["refreshed"][0]["added_tools"] == ["fake_metrics"]

            tools = await client.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert "fake_stdio.fake_status" in tool_names
            assert "fake_stdio.fake_metrics" in tool_names

            metrics = await client.call_tool("fake_stdio.fake_metrics", {})
            metrics_payload = parse_result_payload(metrics)
            assert metrics_payload["response"]["requests"] == 3

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_run_tool_batch_composes_mounted_tools_in_single_call(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 2},
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            batch = await client.call_tool(
                "run_tool_batch",
                {
                    "steps": [
                        {"id": "status", "tool": "fake_stdio.fake_status"},
                        {
                            "id": "metrics",
                            "tool": "fake_stdio.fake_metrics",
                            "arguments": {
                                "arguments": {
                                    "prior_status": {"$from": "status.response.status"},
                                    "prior_version": {"$from": "status.response.version"},
                                }
                            },
                        },
                    ],
                    "final_step": "metrics",
                },
            )
            batch_payload = parse_result_payload(batch)

            assert batch_payload["stopped_on_error"] is False
            assert batch_payload["completed_steps"] == 2
            assert batch_payload["final_step"] == "metrics"
            assert batch_payload["final_data"]["arguments"]["prior_status"] == "ok"
            assert batch_payload["final_data"]["arguments"]["prior_version"] == 2
            assert batch_payload["final_data"]["response"]["requests"] == 3

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_run_tool_program_executes_scripted_composition(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 2},
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    program = """
status = call_tool("fake_stdio.fake_status")
if status.response.status == expected_status:
    metrics = call_tool(
        "fake_stdio.fake_metrics",
        {
            "arguments": {
                "prior_status": status.response.status,
                "prior_version": status.response.version,
            }
        },
    )
    output = {
        "status": status.response.status,
        "version": status.response.version,
        "requests": metrics.response.requests,
        "echo_status": metrics.arguments.prior_status,
    }
else:
    output = {"status": "unexpected"}
""".strip()

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            program_result = await client.call_tool(
                "run_tool_program",
                {
                    "program": program,
                    "initial_context": {"expected_status": "ok"},
                    "result_variable": "output",
                },
            )
            payload = parse_result_payload(program_result)

            assert payload["call_count"] == 2
            assert payload["result_variable"] == "output"
            assert payload["final_data"]["status"] == "ok"
            assert payload["final_data"]["version"] == 2
            assert payload["final_data"]["requests"] == 3
            assert payload["final_data"]["echo_status"] == "ok"

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_run_tool_program_supports_looped_composition_and_helpers(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_catalog",
                "title": "Fake Catalog",
                "description": "Return a list of fake status entries.",
                "response": {
                    "statuses": [
                        {"status": "beta", "version": 2},
                        {"status": "alpha", "version": 1},
                    ]
                },
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    program = """
catalog = call_tool("fake_stdio.fake_catalog")
versions = []
statuses = []
request_counts = []

for item in catalog.response.statuses:
    metric = call_tool(
        "fake_stdio.fake_metrics",
        {"arguments": {"status": item.status, "version": item.version}},
    )
    versions += [item.version]
    statuses += [metric.arguments.status]
    request_counts += [metric.response.requests]

summary_pairs = []
for key, value in items({"count": len(statuses), "request_total": sum(request_counts)}):
    summary_pairs += [[key, value]]

output = {
    "statuses": sorted(statuses),
    "version_total": sum(versions),
    "request_total": sum(request_counts),
    "ordinals": range(1, len(statuses) + 1),
    "summary_pairs": sorted(summary_pairs),
}
""".strip()

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            program_result = await client.call_tool(
                "run_tool_program",
                {
                    "program": program,
                    "result_variable": "output",
                },
            )
            payload = parse_result_payload(program_result)

            assert payload["call_count"] == 3
            assert payload["result_variable"] == "output"
            assert payload["final_data"]["statuses"] == ["alpha", "beta"]
            assert payload["final_data"]["version_total"] == 3
            assert payload["final_data"]["request_total"] == 6
            assert payload["final_data"]["ordinals"] == [1, 2]
            assert payload["final_data"]["summary_pairs"] == [["count", 2], ["request_total", 6]]

            deactivation = await client.call_tool("deactivate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})
            deactivation_payload = parse_result_payload(deactivation)
            assert deactivation_payload["failed"] == []
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_run_tool_program_can_return_requested_contract_summaries(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    service = ToolboxService(state_path=state_path)
    manifest_path = service.fake_manifest_path
    write_manifest(
        manifest_path,
        [
            {
                "name": "fake_status",
                "title": "Fake Status",
                "description": "Return the current fake status.",
                "response": {"status": "ok", "version": 2},
            },
            {
                "name": "fake_metrics",
                "title": "Fake Metrics",
                "description": "Return fake metrics.",
                "response": {"requests": 3},
            },
        ],
    )

    record = service.store.get_toolset("fake_stdio")
    assert record is not None
    record.transport.command = sys.executable
    record.transport.args = ["-m", "toolbox.fake_managed_server", "--manifest", str(manifest_path)]
    record.transport.cwd = str(tmp_path)
    record.transport.env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    service.store.upsert_toolset(record)

    server = create_server(state_path=state_path)
    server.toolbox_service.fake_manifest_path = manifest_path
    server.toolbox_service.store.upsert_toolset(record)

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("activate_toolsets", {"namespaces": ["fake_stdio"], "scope": "thread"})

            program_result = await client.call_tool(
                "run_tool_program",
                {
                    "program": """
status = call_tool("fake_stdio.fake_status")
summary = {"status": status.response.status}
""".strip(),
                    "result_variable": "summary",
                    "return_variables": ["status"],
                    "return_contract_summaries": ["fake_stdio.fake_status", "fake_stdio.fake_missing"],
                },
            )
            payload = parse_result_payload(program_result)

            assert payload["success"] is True
            assert payload["variables"] == {"status": {"arguments": {}, "response": {"status": "ok", "version": 2}, "tool": "fake_status"}}
            assert payload["contract_summaries"][0]["mounted_name"] == "fake_stdio.fake_status"
            assert payload["contract_summaries"][0]["tool_hash"].startswith("sha256:")
            assert payload["missing_contract_summaries"] == [
                {"mounted_name": "fake_stdio.fake_missing", "reason": "tool_not_mounted"}
            ]
    finally:
        await shutdown_server(server)
        del server


@pytest.mark.asyncio
async def test_server_run_tool_program_returns_structured_invalid_program_error(tmp_path: Path) -> None:
    state_path = tmp_path / ".toolbox" / "state.json"
    server = create_server(state_path=state_path)

    try:
        async with create_connected_server_and_client_session(server) as client:
            program_result = await client.call_tool(
                "run_tool_program",
                {
                    "program": "for value in range(3)\n    pass",
                },
            )
            assert program_result.isError is False
            payload = parse_result_payload(program_result)

            assert payload["success"] is False
            assert payload["error"]["code"] == "invalid_program"
    finally:
        await shutdown_server(server)
        del server

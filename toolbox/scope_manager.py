from __future__ import annotations

from toolbox.models import Scope, ToolsetRecord

_SCOPE_ORDER = {
    Scope.THREAD: 0,
    Scope.SESSION: 1,
    Scope.GLOBAL: 2,
}


class ScopeManager:
    """Pure helpers for scope manipulation on toolset records."""

    @staticmethod
    def startup_restore_scopes(
        record: ToolsetRecord,
        *,
        restore_enabled: bool,
        stable_host_identity: bool,
    ) -> list[Scope]:
        if not restore_enabled:
            return []
        if record.restore_requires_identity and not stable_host_identity:
            return []
        if record.restore_requires_explicit_request:
            return []
        return [scope for scope in record.restorable_scopes if scope in record.supported_scopes]

    @staticmethod
    def recovery_scopes(
        record: ToolsetRecord,
        *,
        stable_host_identity: bool,
        explicit_request: bool,
    ) -> list[Scope]:
        if record.restore_requires_identity and not stable_host_identity:
            return []
        if record.restore_requires_explicit_request and not explicit_request:
            return []
        return [
            scope
            for scope in record.recoverable_scopes
            if scope in record.restorable_scopes and scope in record.supported_scopes
        ]

    @staticmethod
    def supports_scope(record: ToolsetRecord, scope: Scope) -> bool:
        return scope in record.supported_scopes

    @staticmethod
    def scope_policy(record: ToolsetRecord) -> dict[str, object]:
        restorable = set(record.restorable_scopes)
        return {
            "default_scope": record.default_scope.value,
            "loaded_scopes": [scope.value for scope in record.loaded_scopes],
            "supported_scopes": [scope.value for scope in record.supported_scopes],
            "restorable_scopes": [scope.value for scope in record.restorable_scopes],
            "recoverable_scopes": [scope.value for scope in record.recoverable_scopes],
            "non_restorable_scopes": [
                scope.value for scope in record.supported_scopes if scope not in restorable
            ],
            "restore_requires_identity": record.restore_requires_identity,
            "restore_requires_explicit_request": record.restore_requires_explicit_request,
        }

    @staticmethod
    def add_scope(record: ToolsetRecord, scope: Scope) -> bool:
        if scope in record.loaded_scopes:
            return False
        record.loaded_scopes.append(scope)
        record.loaded_scopes.sort(key=lambda item: _SCOPE_ORDER[item])
        return True

    @staticmethod
    def remove_scope(record: ToolsetRecord, scope: Scope) -> bool:
        if scope not in record.loaded_scopes:
            return False
        record.loaded_scopes = [item for item in record.loaded_scopes if item != scope]
        return True

    @staticmethod
    def add_recoverable_scope(record: ToolsetRecord, scope: Scope) -> bool:
        if scope not in record.restorable_scopes or scope in record.recoverable_scopes:
            return False
        record.recoverable_scopes.append(scope)
        record.recoverable_scopes.sort(key=lambda item: _SCOPE_ORDER[item])
        return True

    @staticmethod
    def remove_recoverable_scope(record: ToolsetRecord, scope: Scope) -> bool:
        if scope not in record.recoverable_scopes:
            return False
        record.recoverable_scopes = [item for item in record.recoverable_scopes if item != scope]
        return True

    @staticmethod
    def is_loaded(record: ToolsetRecord, scope: Scope | None = None) -> bool:
        if scope is None:
            return bool(record.loaded_scopes)
        return scope in record.loaded_scopes

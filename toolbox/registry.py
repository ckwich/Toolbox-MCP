from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Callable, TypeVar

from toolbox.models import (
    AuditEvent,
    AuditOperation,
    AuditOutcome,
    SchemaSnapshot,
    StateDocument,
    StoredAuditEvent,
    ToolsetRecord,
)
from toolbox.transport_secrets import TransportSecretManager, TransportSecretUnavailableError


T = TypeVar("T")
DEFAULT_AUDIT_RETENTION_LIMIT = 250


class JsonStateStore:
    """Simple JSON persistence for toolset registry and schema cache."""

    def __init__(self, path: Path, *, audit_retention_limit: int = DEFAULT_AUDIT_RETENTION_LIMIT):
        self.path = path
        self._lock = Lock()
        self.audit_retention_limit = audit_retention_limit
        self._transport_secrets = TransportSecretManager(self.path)

    def ensure(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.save(StateDocument())

    def load(self) -> StateDocument:
        self.ensure()
        with self._lock:
            return self._load_unlocked()

    def save(self, document: StateDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._write_unlocked(document)

    def _load_unlocked(self) -> StateDocument:
        raw_payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload, migrated = self._restore_payload(raw_payload)
        document = StateDocument.model_validate(payload)
        if migrated:
            self._write_unlocked(document)
        return document

    def _write_unlocked(self, document: StateDocument) -> None:
        payload = self._serialize_document(document)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, self.path)

    def _restore_payload(self, payload: dict[str, object]) -> tuple[dict[str, object], bool]:
        restored = dict(payload)
        migrated = payload.get("version") != StateDocument().version
        toolsets = []

        for toolset in payload.get("toolsets", []):
            if not isinstance(toolset, dict):
                toolsets.append(toolset)
                continue

            restored_toolset = dict(toolset)
            transport = toolset.get("transport")
            if isinstance(transport, dict):
                try:
                    restored_transport, _ = self._transport_secrets.restore_transport(transport)
                    restored_toolset["transport"] = restored_transport
                    migrated = migrated or self._transport_secrets.needs_migration(transport)
                except TransportSecretUnavailableError as exc:
                    restored_toolset["transport"] = self._transport_with_unavailable_secret_dropped(transport)
                    restored_toolset["transport_state"] = "failed"
                    restored_toolset["stale"] = True
                    restored_toolset["stale_reason"] = "transport_secret_unavailable"
                    restored_toolset["last_error"] = {
                        "code": "transport_secret_unavailable",
                        "message": str(exc),
                        "namespace": restored_toolset.get("namespace"),
                        "retryable": False,
                        "details": {"provider": exc.provider},
                    }
                    migrated = True
            toolsets.append(restored_toolset)

        restored["toolsets"] = toolsets
        restored["version"] = StateDocument().version
        return restored, migrated

    def _serialize_document(self, document: StateDocument) -> dict[str, object]:
        payload = document.model_dump(mode="json")
        for toolset in payload.get("toolsets", []):
            if not isinstance(toolset, dict):
                continue
            transport = toolset.get("transport")
            if isinstance(transport, dict):
                toolset["transport"] = self._transport_secrets.protect_transport(transport)
        payload["version"] = StateDocument().version
        return payload

    @staticmethod
    def _transport_with_unavailable_secret_dropped(transport: dict[str, object]) -> dict[str, object]:
        restored = dict(transport)
        restored.pop("secret_envelope", None)
        restored["args"] = []
        restored["env"] = {}
        return restored

    def _mutate(self, fn: Callable[[StateDocument], T]) -> T:
        self.ensure()
        with self._lock:
            document = self._load_unlocked()
            result = fn(document)
            self._write_unlocked(document)
            return result

    def list_toolsets(self) -> list[ToolsetRecord]:
        return self.load().toolsets

    def get_toolset(self, namespace: str) -> ToolsetRecord | None:
        document = self.load()
        for record in document.toolsets:
            if record.namespace == namespace:
                return record
        return None

    def upsert_toolset(self, record: ToolsetRecord) -> ToolsetRecord:
        def mutate(document: StateDocument) -> ToolsetRecord:
            updated = False
            for index, existing in enumerate(document.toolsets):
                if existing.namespace == record.namespace:
                    document.toolsets[index] = record
                    updated = True
                    break
            if not updated:
                document.toolsets.append(record)
            return record

        return self._mutate(mutate)

    def delete_toolset(self, namespace: str) -> bool:
        def mutate(document: StateDocument) -> bool:
            remaining = [record for record in document.toolsets if record.namespace != namespace]
            if len(remaining) == len(document.toolsets):
                return False
            document.toolsets = remaining
            return True

        return self._mutate(mutate)

    def get_schema_snapshot(self, namespace: str) -> SchemaSnapshot | None:
        document = self.load()
        for snapshot in document.schema_cache:
            if snapshot.namespace == namespace:
                return snapshot
        return None

    def get_previous_schema_snapshot(self, namespace: str) -> SchemaSnapshot | None:
        document = self.load()
        for snapshot in document.previous_schema_cache:
            if snapshot.namespace == namespace:
                return snapshot
        return None

    def upsert_schema_snapshot(self, snapshot: SchemaSnapshot) -> SchemaSnapshot:
        def mutate(document: StateDocument) -> SchemaSnapshot:
            updated = False
            for index, existing in enumerate(document.schema_cache):
                if existing.namespace == snapshot.namespace:
                    document.schema_cache[index] = snapshot
                    updated = True
                    break
            if not updated:
                document.schema_cache.append(snapshot)
            return snapshot

        return self._mutate(mutate)

    def replace_schema_snapshot(self, snapshot: SchemaSnapshot) -> SchemaSnapshot:
        def mutate(document: StateDocument) -> SchemaSnapshot:
            existing_current = next(
                (existing for existing in document.schema_cache if existing.namespace == snapshot.namespace),
                None,
            )
            if existing_current is not None:
                previous_updated = False
                for index, existing_previous in enumerate(document.previous_schema_cache):
                    if existing_previous.namespace == snapshot.namespace:
                        document.previous_schema_cache[index] = existing_current
                        previous_updated = True
                        break
                if not previous_updated:
                    document.previous_schema_cache.append(existing_current)

            updated = False
            for index, existing in enumerate(document.schema_cache):
                if existing.namespace == snapshot.namespace:
                    document.schema_cache[index] = snapshot
                    updated = True
                    break
            if not updated:
                document.schema_cache.append(snapshot)
            return snapshot

        return self._mutate(mutate)

    def delete_schema_snapshot(self, namespace: str) -> bool:
        def mutate(document: StateDocument) -> bool:
            remaining = [snapshot for snapshot in document.schema_cache if snapshot.namespace != namespace]
            if len(remaining) == len(document.schema_cache):
                return False
            document.schema_cache = remaining
            return True

        return self._mutate(mutate)

    def delete_previous_schema_snapshot(self, namespace: str) -> bool:
        def mutate(document: StateDocument) -> bool:
            remaining = [snapshot for snapshot in document.previous_schema_cache if snapshot.namespace != namespace]
            if len(remaining) == len(document.previous_schema_cache):
                return False
            document.previous_schema_cache = remaining
            return True

        return self._mutate(mutate)

    def append_audit_event(self, event: StoredAuditEvent) -> AuditEvent:
        def mutate(document: StateDocument) -> AuditEvent:
            stored = event.model_copy(update={"sequence": document.next_audit_sequence})
            document.next_audit_sequence += 1
            document.audit_log.append(stored)
            if len(document.audit_log) > self.audit_retention_limit:
                document.audit_log = document.audit_log[-self.audit_retention_limit :]
            return self._to_audit_event(stored, document)

        return self._mutate(mutate)

    def list_audit_events(
        self,
        *,
        namespace: str | None = None,
        operation: AuditOperation | str | None = None,
        outcome: AuditOutcome | str | None = None,
        registration_present: bool | None = None,
        limit: int | None = None,
    ) -> list[AuditEvent]:
        document = self.load()
        selected: list[AuditEvent] = []
        operation_value = operation.value if isinstance(operation, AuditOperation) else operation
        outcome_value = outcome.value if isinstance(outcome, AuditOutcome) else outcome

        for stored in document.audit_log:
            event = self._to_audit_event(stored, document)
            if namespace is not None and event.namespace != namespace:
                continue
            if operation_value is not None and event.operation.value != operation_value:
                continue
            if outcome_value is not None and event.outcome.value != outcome_value:
                continue
            if registration_present is not None and event.registration_present is not registration_present:
                continue
            selected.append(event)

        if limit is not None:
            if limit <= 0:
                return []
            return selected[-limit:]
        return selected

    @staticmethod
    def _to_audit_event(event: StoredAuditEvent, document: StateDocument) -> AuditEvent:
        namespaces = {record.namespace for record in document.toolsets}
        registration_present = event.namespace in namespaces if event.namespace is not None else None
        return AuditEvent(
            sequence=event.sequence,
            timestamp=event.timestamp,
            operation=event.operation,
            outcome=event.outcome,
            namespace=event.namespace,
            scope=event.scope,
            registration_present=registration_present,
            details=event.details,
            error=event.error,
        )

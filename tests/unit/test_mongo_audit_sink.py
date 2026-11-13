from datetime import UTC, datetime

import pytest
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from medicalbooking.modules.replica.application.fallback_read import (
    AuditRecord,
    AuditUnavailable,
)
from medicalbooking.modules.replica.infrastructure.mongo_audit import MongoAuditSink


class FakeCollection:
    def __init__(self, error: Exception | None = None) -> None:
        self.inserted: list[dict] = []
        self.other_calls: list[str] = []
        self._error = error

    def insert_one(self, document: dict):
        if self._error is not None:
            raise self._error
        self.inserted.append(document)

    def update_one(self, *args, **kwargs):
        self.other_calls.append("update_one")

    def replace_one(self, *args, **kwargs):
        self.other_calls.append("replace_one")

    def delete_one(self, *args, **kwargs):
        self.other_calls.append("delete_one")


def _record() -> AuditRecord:
    return AuditRecord(
        fecha_evento=datetime(2026, 11, 12, 18, 0, tzinfo=UTC),
        paciente_id="11111111-1111-1111-1111-111111111111",
        actor_id="22222222-2222-2222-2222-222222222222",
        accion="READ_CONTINGENCY",
        entidad="resultados_laboratorio",
        resultado="AUTORIZADA",
        correlation_id="33333333-3333-3333-3333-333333333333",
        ip_origen="10.0.0.5",
    )


def test_the_sink_inserts_exactly_the_audit_document():
    collection = FakeCollection()

    MongoAuditSink(collection).append(_record())

    assert collection.inserted == [_record().as_document()]


def test_the_sink_never_updates_replaces_or_deletes_audit_history():
    collection = FakeCollection()

    MongoAuditSink(collection).append(_record())

    assert collection.other_calls == []


def test_a_rejected_insert_is_reported_as_audit_unavailable():
    collection = FakeCollection(error=OperationFailure("not authorized"))

    with pytest.raises(AuditUnavailable):
        MongoAuditSink(collection).append(_record())


def test_an_unreachable_cluster_is_reported_as_audit_unavailable():
    collection = FakeCollection(error=ServerSelectionTimeoutError("no primary"))

    with pytest.raises(AuditUnavailable):
        MongoAuditSink(collection).append(_record())

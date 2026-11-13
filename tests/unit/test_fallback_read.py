from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.replica.application.fallback_read import (
    AuditRecord,
    AuditUnavailable,
    FallbackReadService,
    PatientResults,
)

PATIENT = UUID("11111111-1111-1111-1111-111111111111")
ACTOR = UUID("22222222-2222-2222-2222-222222222222")
CORRELATION = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[AuditRecord] = []
        self._fail = fail

    def append(self, record: AuditRecord) -> None:
        if self._fail:
            raise AuditUnavailable("audit store down")
        self.records.append(record)


class RecordingReader:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults:
        self.calls.append(patient_id)
        return PatientResults(patient_id=patient_id, orders=())


def _service(sink, reader) -> FallbackReadService:
    return FallbackReadService(sink=sink, reader=reader, clock=lambda: NOW)


def test_the_audit_record_is_written_before_the_read():
    order_of_events: list[str] = []

    class OrderedSink(RecordingSink):
        def append(self, record):
            order_of_events.append("audit")
            super().append(record)

    class OrderedReader(RecordingReader):
        def results_of(self, patient_id, usuario_id):
            order_of_events.append("read")
            return super().results_of(patient_id, usuario_id)

    _service(OrderedSink(), OrderedReader()).results_for(
        actor_id=ACTOR, patient_id=PATIENT, correlation_id=CORRELATION, ip="10.0.0.5"
    )

    assert order_of_events == ["audit", "read"]


def test_a_failed_audit_rejects_the_read_and_never_touches_the_replica():
    reader = RecordingReader()

    with pytest.raises(AuditUnavailable):
        _service(RecordingSink(fail=True), reader).results_for(
            actor_id=ACTOR, patient_id=PATIENT, correlation_id=CORRELATION, ip="10.0.0.5"
        )

    assert reader.calls == []


def test_the_audit_record_has_only_the_allowed_fields_and_no_clinical_values():
    sink = RecordingSink()

    _service(sink, RecordingReader()).results_for(
        actor_id=ACTOR, patient_id=PATIENT, correlation_id=CORRELATION, ip="10.0.0.5"
    )

    record = sink.records[0]
    assert set(record.as_document()) == {
        "fecha_evento",
        "paciente_id",
        "actor_id",
        "accion",
        "entidad",
        "resultado",
        "correlation_id",
        "ip_origen",
    }
    assert record.paciente_id == str(PATIENT)
    assert record.actor_id == str(ACTOR)
    assert record.correlation_id == str(CORRELATION)
    assert record.accion == "READ_CONTINGENCY"
    assert record.entidad == "resultados_laboratorio"
    assert record.resultado == "AUTORIZADA"
    assert record.fecha_evento == NOW


def test_the_results_come_back_when_the_audit_is_written():
    reader = RecordingReader()

    result = _service(RecordingSink(), reader).results_for(
        actor_id=ACTOR, patient_id=PATIENT, correlation_id=CORRELATION, ip=None
    )

    assert result.patient_id == PATIENT
    assert reader.calls == [PATIENT]

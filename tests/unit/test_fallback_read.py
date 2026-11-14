from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AuditRecord,
    AuditUnavailable,
    FallbackReadService,
    PatientResults,
)

PATIENT = UUID("11111111-1111-1111-1111-111111111111")
ACTOR = UUID("22222222-2222-2222-2222-222222222222")
CORRELATION = UUID("33333333-3333-3333-3333-333333333333")
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


class Log:
    def __init__(self) -> None:
        self.events: list[str] = []


class RecordingSink:
    def __init__(self, log: Log, fail: bool = False) -> None:
        self.records: list[AuditRecord] = []
        self._log = log
        self._fail = fail

    def append(self, record: AuditRecord) -> None:
        self._log.events.append("audit")
        if self._fail:
            raise AuditUnavailable("audit store down")
        self.records.append(record)


class RecordingReader:
    def __init__(self, log: Log) -> None:
        self.calls: list[tuple[UUID, UUID]] = []
        self._log = log

    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults:
        self._log.events.append("read")
        self.calls.append((patient_id, usuario_id))
        return PatientResults(patient_id=patient_id, orders=())


class RecordingResolver:
    def __init__(self, log: Log, deny: bool = False) -> None:
        self.calls: list[UUID] = []
        self._log = log
        self._deny = deny

    def patient_for(self, usuario_id: UUID) -> UUID:
        self._log.events.append("resolve")
        self.calls.append(usuario_id)
        if self._deny:
            raise AccessDenied("no own-results permission")
        return PATIENT


def _service(log: Log, *, sink=None, reader=None, resolver=None) -> FallbackReadService:
    return FallbackReadService(
        sink=sink or RecordingSink(log),
        reader=reader or RecordingReader(log),
        resolver=resolver or RecordingResolver(log),
        clock=lambda: NOW,
    )


def test_the_patient_is_resolved_then_audited_then_read():
    log = Log()

    _service(log).results_for(actor_id=ACTOR, correlation_id=CORRELATION, ip="10.0.0.5")

    assert log.events == ["resolve", "audit", "read"]


def test_a_denied_patient_is_never_audited_as_authorized_nor_read():
    log = Log()
    reader = RecordingReader(log)
    sink = RecordingSink(log)

    with pytest.raises(AccessDenied):
        _service(
            log, sink=sink, reader=reader, resolver=RecordingResolver(log, deny=True)
        ).results_for(actor_id=ACTOR, correlation_id=CORRELATION, ip=None)

    assert [r.resultado for r in sink.records] == ["DENEGADA"]
    assert sink.records[0].paciente_id is None
    assert sink.records[0].actor_id == str(ACTOR)
    assert reader.calls == []


def test_a_failed_audit_rejects_the_read_and_never_touches_the_replica():
    log = Log()
    reader = RecordingReader(log)

    with pytest.raises(AuditUnavailable):
        _service(log, sink=RecordingSink(log, fail=True), reader=reader).results_for(
            actor_id=ACTOR, correlation_id=CORRELATION, ip="10.0.0.5"
        )

    assert reader.calls == []


def test_the_audit_record_has_only_the_allowed_fields_and_no_clinical_values():
    log = Log()
    sink = RecordingSink(log)

    _service(log, sink=sink).results_for(actor_id=ACTOR, correlation_id=CORRELATION, ip="10.0.0.5")

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


def test_the_read_uses_the_resolved_patient_and_the_actor_account():
    log = Log()
    reader = RecordingReader(log)

    result = _service(log, reader=reader).results_for(
        actor_id=ACTOR, correlation_id=CORRELATION, ip=None
    )

    assert result.patient_id == PATIENT
    assert reader.calls == [(PATIENT, ACTOR)]


def test_a_denial_that_cannot_be_audited_is_still_refused_without_data():
    log = Log()
    reader = RecordingReader(log)

    with pytest.raises(AccessDenied):
        _service(
            log,
            sink=RecordingSink(log, fail=True),
            reader=reader,
            resolver=RecordingResolver(log, deny=True),
        ).results_for(actor_id=ACTOR, correlation_id=CORRELATION, ip=None)

    assert reader.calls == []


def test_a_denial_record_carries_the_same_fields_and_no_patient_or_clinical_data():
    log = Log()
    sink = RecordingSink(log)

    with pytest.raises(AccessDenied):
        _service(log, sink=sink, resolver=RecordingResolver(log, deny=True)).results_for(
            actor_id=ACTOR, correlation_id=CORRELATION, ip="10.0.0.5"
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
    assert record.accion == "READ_CONTINGENCY"
    assert record.resultado == "DENEGADA"
    assert record.paciente_id is None
    assert record.correlation_id == str(CORRELATION)
    assert record.ip_origen == "10.0.0.5"


def test_an_unaudited_denial_is_logged_so_the_gap_is_visible(caplog):
    log = Log()

    with pytest.raises(AccessDenied), caplog.at_level("ERROR"):
        _service(
            log, sink=RecordingSink(log, fail=True), resolver=RecordingResolver(log, deny=True)
        ).results_for(actor_id=ACTOR, correlation_id=CORRELATION, ip=None)

    assert any("denial not audited" in message for message in caplog.messages)

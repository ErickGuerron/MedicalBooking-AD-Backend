"""Read-only doctor work hours during an outage (replica-work-hours S4).

Work hours are staff schedules, not patient data. The read follows the same order as the
results read: the account must be active, the read is audited (fail closed), and only then the
replica is read. Each doctor gets the week in force on the requested day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.replica.application.fallback_read import (
    AccountNotActive,
    AuditRecord,
    AuditUnavailable,
    FallbackReadService,
)
from medicalbooking.modules.replica.application.horarios_contingencia import vigente_por_medico

MEDICO_A = "11111111-1111-4111-8111-111111111111"
MEDICO_B = "22222222-2222-4222-8222-222222222222"
ACTOR = UUID("33333333-3333-4333-8333-333333333333")
BLOQUES = [{"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"}]
NUEVOS = [{"dia_semana": 1, "inicio": "14:00:00", "fin": "16:00:00"}]


def test_each_doctor_gets_the_latest_week_not_after_the_day() -> None:
    rows = [
        (MEDICO_A, "Ana", "Lopez", date(2026, 11, 2), BLOQUES),
        (MEDICO_A, "Ana", "Lopez", date(2026, 11, 30), NUEVOS),
        (MEDICO_B, "Luis", "Perez", date(2026, 12, 7), BLOQUES),
    ]

    vigentes = vigente_por_medico(rows, on=date(2026, 11, 23))

    assert vigentes == [
        {
            "medico_id": MEDICO_A,
            "nombre": "Ana",
            "apellido": "Lopez",
            "vigente_desde": "2026-11-02",
            "bloques": BLOQUES,
        }
    ]


def test_a_week_starting_on_the_day_is_in_force() -> None:
    vigentes = vigente_por_medico(
        [(MEDICO_A, "Ana", "Lopez", date(2026, 11, 23), NUEVOS)], on=date(2026, 11, 23)
    )

    assert vigentes[0]["bloques"] == NUEVOS


class _Sink:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[AuditRecord] = []
        self._fail = fail

    def append(self, record: AuditRecord) -> None:
        if self._fail:
            raise AuditUnavailable("audit store down")
        self.records.append(record)


class _Reader:
    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.read_on: list[date] = []

    def assert_active(self, usuario_id: UUID) -> None:
        if not self.active:
            raise AccountNotActive("account not active in the replica")

    def doctor_schedules(self, on: date) -> list[dict]:
        self.read_on.append(on)
        return [{"medico_id": MEDICO_A, "bloques": BLOQUES}]


class _Resolver:
    def patient_for(self, usuario_id: UUID) -> UUID:  # pragma: no cover - not used here
        raise AssertionError("the schedules read does not resolve a patient")


def _service(sink: _Sink, reader: _Reader) -> FallbackReadService:
    return FallbackReadService(
        sink=sink,
        reader=reader,
        resolver=_Resolver(),
        clock=lambda: datetime(2026, 11, 23, 15, 0, tzinfo=UTC),
    )


def test_schedules_are_audited_before_they_are_read() -> None:
    sink, reader = _Sink(), _Reader()

    result = _service(sink, reader).schedules_for(ACTOR, UUID(int=1), "10.0.0.1")

    assert result == [{"medico_id": MEDICO_A, "bloques": BLOQUES}]
    assert [r.accion for r in sink.records] == ["READ_CONTINGENCY"]
    assert sink.records[0].entidad == "horario_trabajo"
    assert sink.records[0].resultado == "AUTORIZADA"
    assert reader.read_on == [date(2026, 11, 23)]


def test_inactive_account_is_refused_audited_as_denied_and_nothing_is_read() -> None:
    sink, reader = _Sink(), _Reader(active=False)

    with pytest.raises(AccountNotActive):
        _service(sink, reader).schedules_for(ACTOR, UUID(int=1), None)

    assert [r.resultado for r in sink.records] == ["DENEGADA"]
    assert sink.records[0].paciente_id is None
    assert reader.read_on == []


def test_unavailable_audit_fails_closed_and_reads_nothing() -> None:
    sink, reader = _Sink(fail=True), _Reader()

    with pytest.raises(AuditUnavailable):
        _service(sink, reader).schedules_for(ACTOR, UUID(int=1), None)

    assert reader.read_on == []

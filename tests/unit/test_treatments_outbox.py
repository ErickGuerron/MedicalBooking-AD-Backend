"""Outbox events for tratamientos and seguimientos (treatments S11).

The snapshots are the replicated shape; the emit helpers append one event on the caller's SQL
connection, so the event commits or rolls back with the write it describes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from medicalbooking.modules.treatments.application.ports import FollowUp, Treatment
from medicalbooking.modules.treatments.infrastructure import outbox_events
from medicalbooking.modules.treatments.infrastructure.outbox_events import (
    emit_followup_event,
    emit_treatment_event,
    followup_snapshot,
    treatment_snapshot,
)
from medicalbooking.shared.infrastructure.outbox import append_outbox

TRATAMIENTO = "11111111-1111-4111-8111-111111111111"
SEGUIMIENTO = "22222222-2222-4222-8222-222222222222"
CITA = "33333333-3333-4333-8333-333333333333"
PACIENTE = "44444444-4444-4444-8444-444444444444"
MEDICO = "55555555-5555-4555-8555-555555555555"
PACIENTE_USER = "66666666-6666-4666-8666-666666666666"
MEDICO_USER = "77777777-7777-4777-8777-777777777777"
DESCRIPCION = "Fisioterapia lumbar, control semanal con ejercicios de movilidad (sintetico)"
NOTA = "Paciente refiere mejoria del dolor; continuar ejercicios de movilidad (sintetico)."
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _treatment(**overrides: Any) -> Treatment:
    values: dict[str, Any] = {
        "id": TRATAMIENTO,
        "cita_id": CITA,
        "paciente_id": PACIENTE,
        "paciente_user_id": PACIENTE_USER,
        "medico_id": MEDICO,
        "medico_user_id": MEDICO_USER,
        "descripcion": DESCRIPCION,
        "estado": "ACTIVO",
        "fecha_inicio": date(2026, 11, 1),
        "fecha_fin": None,
    }
    values.update(overrides)
    return Treatment(**values)


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []


def test_treatment_snapshot_carries_the_full_description_and_only_the_replicated_fields() -> None:
    assert treatment_snapshot(_treatment()) == {
        "id": TRATAMIENTO,
        "cita_id": CITA,
        "paciente_id": PACIENTE,
        "medico_id": MEDICO,
        "descripcion": DESCRIPCION,
        "estado": "ACTIVO",
        "fecha_inicio": "2026-11-01",
        "fecha_fin": None,
    }


def test_followup_snapshot_carries_the_full_note_and_its_sequence() -> None:
    followup = FollowUp(id=SEGUIMIENTO, tratamiento_id=TRATAMIENTO, nota=NOTA, registrado_en=NOW)

    assert followup_snapshot(followup, 7) == {
        "id": SEGUIMIENTO,
        "tratamiento_id": TRATAMIENTO,
        "secuencia": 7,
        "nota": NOTA,
        "creado_en": NOW.isoformat(),
    }


def test_a_treatment_write_appends_exactly_one_event_on_the_same_connection(monkeypatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(outbox_events, "append_outbox", lambda *args: calls.append(args))
    connection = _RecordingConnection()

    emit_treatment_event(connection, "tratamiento.creado", _treatment(), NOW)

    assert calls == [
        (
            connection,
            "tratamiento",
            TRATAMIENTO,
            "tratamiento.creado",
            {"tratamiento": treatment_snapshot(_treatment())},
            NOW,
        )
    ]


def test_a_followup_appends_seguimiento_registrado_with_the_full_note(monkeypatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(outbox_events, "append_outbox", lambda *args: calls.append(args))
    connection = _RecordingConnection()
    followup = FollowUp(id=SEGUIMIENTO, tratamiento_id=TRATAMIENTO, nota=NOTA, registrado_en=NOW)

    emit_followup_event(connection, followup, 7, NOW)

    assert calls == [
        (
            connection,
            "tratamiento",
            TRATAMIENTO,
            "seguimiento.registrado",
            {"seguimiento": followup_snapshot(followup, 7)},
            NOW,
        )
    ]


class _Result:
    def __init__(self, value: Any = None) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _SqlConnection:
    """Records statements. The version query already returns MAX + 1, so 4 is the next version."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        if "MAX(version_agregado)" in str(statement):
            return _Result(4)
        return _Result()


def test_shared_outbox_helper_still_numbers_each_aggregate_by_its_next_version() -> None:
    connection = _SqlConnection()

    append_outbox(connection, "tratamiento", TRATAMIENTO, "tratamiento.actualizado", {}, NOW)

    insert_sql, insert_params = connection.statements[-1]
    assert insert_sql.lstrip().startswith("INSERT INTO eventos_outbox")
    assert insert_params["version"] == 4
    assert insert_params["t"] == "tratamiento"
    assert insert_params["a"] == TRATAMIENTO
    assert insert_params["tipo"] == "tratamiento.actualizado"

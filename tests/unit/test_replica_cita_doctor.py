"""The replica's appointment projection carries the doctor, and tolerates events without it."""

from __future__ import annotations

from medicalbooking.modules.replica.infrastructure.supabase_projection import _Transaction

CITA = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "paciente_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "servicio_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "estado": "RESERVADA",
    "reservada_en": "2026-11-15T10:00:00+00:00",
    "inicio": "2026-11-18T14:30:00+00:00",
    "fin": "2026-11-18T15:00:00+00:00",
}


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, params))


def _upsert(cita: dict) -> tuple[str, tuple]:
    cursor = _Cursor()
    _Transaction(cursor).upsert_cita(cita)
    return cursor.calls[0]


def test_the_doctor_is_written_with_the_appointment() -> None:
    sql, params = _upsert({**CITA, "medico_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"})

    assert "medico_id" in sql
    assert params[3] == "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def test_an_event_without_the_doctor_still_projects_the_appointment() -> None:
    sql, params = _upsert(CITA)

    assert params[0] == CITA["id"]
    assert params[3] is None
    assert params[4] == "RESERVADA"


def test_a_missing_doctor_keeps_the_doctor_the_replica_already_has() -> None:
    sql, _ = _upsert(CITA)

    assert "COALESCE(excluded.medico_id, replica.citas.medico_id)" in sql

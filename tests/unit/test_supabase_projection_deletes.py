"""Supabase replica deletes: the SQL that usuario.eliminado and cita.eliminada run.

A fake connection records each statement; the sink is the real SupabaseProjectionSink.
"""

from __future__ import annotations

from contextlib import contextmanager

from medicalbooking.modules.replica.application.projection_consumer import apply_event
from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)

USER = "77777777-7777-4777-8777-777777777777"
CITA = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeCursor:
    """Records statements. A DELETE matches no row here, so a missing row is covered."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.rowcount = 1

    def execute(self, sql: str, params: tuple = ()) -> None:
        text = " ".join(sql.split())
        self.statements.append((text, params))
        self.rowcount = 0 if text.startswith("DELETE") else 1


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def cursor(self):
        yield self.cursor_obj

    def close(self) -> None:
        pass


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> dict:
    return {
        "event_id": EVENT_ID,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "aggregate_version": 2,
        "created_at": "2026-11-13T12:00:00+00:00",
        "payload": payload,
    }


def _deletes(connection: FakeConnection) -> list[tuple[str, tuple]]:
    return [entry for entry in connection.cursor_obj.statements if entry[0].startswith("DELETE")]


def test_usuario_eliminado_removes_roles_then_patient_then_account():
    connection = FakeConnection()
    event = _envelope("usuario.eliminado", "usuario", USER, {"usuario": {"id": USER}})

    assert apply_event(event, SupabaseProjectionSink(lambda: connection), "replica-supabase")

    assert _deletes(connection) == [
        ("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (USER,)),
        ("DELETE FROM replica.pacientes WHERE usuario_id = %s", (USER,)),
        ("DELETE FROM replica.usuarios WHERE id = %s", (USER,)),
    ]


def test_cita_eliminada_removes_the_appointment_by_id():
    connection = FakeConnection()
    event = _envelope("cita.eliminada", "cita", CITA, {"cita": {"id": CITA}})

    apply_event(event, SupabaseProjectionSink(lambda: connection), "replica-supabase")

    assert _deletes(connection) == [("DELETE FROM replica.citas WHERE id = %s", (CITA,))]


def test_deleting_a_row_the_replica_does_not_hold_is_not_an_error():
    connection = FakeConnection()
    event = _envelope("cita.eliminada", "cita", CITA, {"cita": {"id": CITA}})

    applied = apply_event(event, SupabaseProjectionSink(lambda: connection), "replica-supabase")

    assert applied is True
    assert connection.cursor_obj.rowcount == 0

"""The shared step that confirms the appointment of a paid invoice (no database).

A fake connection answers the two statements the function runs; the history row and the replica
snapshot are observed through the module-level helpers it calls.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from medicalbooking.modules.scheduling.infrastructure import repositories as module

CITA = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
MOMENT = datetime(2026, 11, 9, 12, 0)


class _Result:
    def __init__(self, scalar=None, rowcount=0) -> None:
        self._scalar, self.rowcount = scalar, rowcount

    def scalar(self):
        return self._scalar


class FakeConnection:
    def __init__(self, cita_id, moved: int) -> None:
        self._cita_id, self._moved = cita_id, moved
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("SELECT cita_id"):
            return _Result(scalar=self._cita_id)
        return _Result(rowcount=self._moved)


@pytest.fixture
def effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"history": [], "snapshot": []}
    monkeypatch.setattr(module, "_history", lambda *args: calls["history"].append(args[1:]))
    monkeypatch.setattr(
        module, "emit_cita_snapshot", lambda *args: calls["snapshot"].append(args[1:])
    )
    return calls


def test_a_pending_appointment_is_confirmed_with_history_and_snapshot(effects) -> None:
    conn = FakeConnection(CITA, moved=1)

    assert module.confirm_appointment_of_invoice(conn, "f", MOMENT) is True

    assert "estado = 'PENDIENTE_PAGO'" in conn.statements[1]
    cita = CITA.lower()
    assert effects["history"] == [(cita, None, "PENDIENTE_PAGO", "CONFIRMADA", MOMENT)]
    assert effects["snapshot"] == [(cita, MOMENT)]


def test_an_appointment_in_another_state_is_left_alone(effects) -> None:
    conn = FakeConnection(CITA, moved=0)

    assert module.confirm_appointment_of_invoice(conn, "f", MOMENT) is False
    assert effects == {"history": [], "snapshot": []}


def test_an_invoice_without_appointment_does_nothing(effects) -> None:
    conn = FakeConnection(None, moved=1)

    assert module.confirm_appointment_of_invoice(conn, "f", MOMENT) is False
    assert len(conn.statements) == 1
    assert effects == {"history": [], "snapshot": []}

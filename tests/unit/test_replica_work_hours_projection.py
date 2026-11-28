"""Consumer routing for declared work hours (replica-work-hours S2).

The fake transaction records the calls. The projection receives the whole week and the envelope
version, so the sink can ignore a stale event.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from medicalbooking.modules.replica.application.projection_consumer import _HANDLERS, apply_event

EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
MEDICO = "55555555-5555-4555-8555-555555555555"


class FakeTx:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def mark_processed(self, event_id, consumer, tipo, created_at) -> bool:
        return True

    def upsert_horario_version(self, horario, bloques, version) -> None:
        self.calls.append(("upsert_horario_version", horario, bloques, version))


class FakeSink:
    def __init__(self) -> None:
        self.tx = FakeTx()

    @contextmanager
    def transaction(self):
        yield self.tx


def _envelope(version: int) -> dict[str, Any]:
    return {
        "event_id": EVENT_ID,
        "aggregate_type": "medico_horario",
        "aggregate_id": MEDICO,
        "event_type": "horario_trabajo.declarado",
        "aggregate_version": version,
        "created_at": "2026-11-23T12:00:00+00:00",
        "payload": {
            "horario": {"medico_id": MEDICO, "vigente_desde": "2026-11-30"},
            "bloques": [{"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"}],
        },
    }


def test_declared_week_is_projected_with_the_envelope_version() -> None:
    sink = FakeSink()

    apply_event(_envelope(version=4), sink, "test-consumer")

    assert sink.tx.calls == [
        (
            "upsert_horario_version",
            {"medico_id": MEDICO, "vigente_desde": "2026-11-30"},
            [{"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"}],
            4,
        )
    ]


def test_existing_dispatch_entries_are_unchanged() -> None:
    assert "horario_trabajo.declarado" in _HANDLERS
    assert "cita.actualizada" in _HANDLERS

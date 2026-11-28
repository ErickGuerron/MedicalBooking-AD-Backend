"""Outbox payload of a declared work-hours week (replica-work-hours S1).

The payload carries the whole block set of one week, sorted, so a replayed event replaces the
week instead of patching it.
"""

from __future__ import annotations

from datetime import date, time

from medicalbooking.modules.scheduling.domain.clinic_hours import WorkBlock, declared_hours_payload

MEDICO = "55555555-5555-4555-8555-555555555555"


def test_payload_carries_the_week_identity_and_every_block_sorted() -> None:
    blocks = [
        WorkBlock(dia_semana=2, inicio=time(14, 0), fin=time(16, 0)),
        WorkBlock(dia_semana=0, inicio=time(12, 0), fin=time(13, 0)),
        WorkBlock(dia_semana=0, inicio=time(8, 0), fin=time(10, 0)),
    ]

    payload = declared_hours_payload(MEDICO, date(2026, 11, 30), blocks)

    assert payload == {
        "horario": {"medico_id": MEDICO, "vigente_desde": "2026-11-30"},
        "bloques": [
            {"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"},
            {"dia_semana": 0, "inicio": "12:00:00", "fin": "13:00:00"},
            {"dia_semana": 2, "inicio": "14:00:00", "fin": "16:00:00"},
        ],
    }


def test_empty_week_is_a_valid_payload_that_clears_the_week() -> None:
    payload = declared_hours_payload(MEDICO, date(2026, 11, 30), [])

    assert payload["bloques"] == []
    assert payload["horario"]["vigente_desde"] == "2026-11-30"

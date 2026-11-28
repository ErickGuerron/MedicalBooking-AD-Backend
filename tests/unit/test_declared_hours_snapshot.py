"""Catalog snapshot of declared work hours (replica-work-hours S3).

The snapshot carries, per doctor and week, the outbox version the source had reached for that
doctor. The replica applies it with the same version guard as the event, so a snapshot taken
during a change never overwrites a newer event.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, time
from typing import Any

from medicalbooking.modules.replica.application.declared_hours_sync import (
    build_declared_hours_snapshot,
    upsert_declared_hours,
)

MEDICO = "55555555-5555-4555-8555-555555555555"
OTRO = "66666666-6666-4666-8666-666666666666"


def test_rows_group_into_weeks_with_sorted_blocks_and_empty_weeks_kept() -> None:
    rows = [
        (MEDICO, date(2026, 11, 30), 2, time(14, 0), time(16, 0), 5),
        (MEDICO, date(2026, 11, 30), 0, time(8, 0), time(10, 0), 5),
        (MEDICO, date(2026, 12, 7), None, None, None, 5),
        (OTRO, date(2026, 11, 30), 1, time(9, 0), time(12, 0), 0),
    ]

    snapshot = build_declared_hours_snapshot(rows)

    weeks = {(w.medico_id, w.vigente_desde): w for w in snapshot.weeks}
    assert weeks[(MEDICO, "2026-11-30")].version == 5
    assert weeks[(MEDICO, "2026-11-30")].bloques == (
        {"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"},
        {"dia_semana": 2, "inicio": "14:00:00", "fin": "16:00:00"},
    )
    assert weeks[(MEDICO, "2026-12-07")].bloques == ()
    assert weeks[(OTRO, "2026-11-30")].version == 0


class _Recorder:
    def __init__(self) -> None:
        self.sql: list[tuple[str, Any]] = []
        self.transactions = 0

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append((sql, params))


class _Target:
    def __init__(self) -> None:
        self.recorder = _Recorder()

    @contextmanager
    def transaction(self):
        self.recorder.transactions += 1
        yield self.recorder


def test_snapshot_is_applied_in_one_transaction_with_the_version_guard() -> None:
    snapshot = build_declared_hours_snapshot(
        [(MEDICO, date(2026, 11, 30), 0, time(8, 0), time(10, 0), 5)]
    )
    target = _Target()

    upsert_declared_hours(snapshot, target)

    assert target.recorder.transactions == 1
    sql, params = target.recorder.sql[0]
    assert "WHERE replica.horarios_trabajo_version.version < excluded.version" in sql
    assert params[0] == MEDICO
    assert params[1] == "2026-11-30"
    assert json.loads(params[2]) == [{"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"}]
    assert params[3] == 5

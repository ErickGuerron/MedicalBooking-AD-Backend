"""MongoDB projection of declared work hours (replica-work-hours S2 and S3 for Atlas).

One document per doctor and week, keyed "<medico>:<vigente_desde>". The event and the snapshot
write with the same guard: a document is replaced only by a version that is not older.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

from pymongo.errors import DuplicateKeyError

from medicalbooking.modules.replica.application.declared_hours_sync import (
    build_declared_hours_snapshot,
    horario_key,
)
from medicalbooking.modules.replica.infrastructure.mongo_declared_hours import (
    upsert_declared_hours_mongo,
)
from medicalbooking.modules.replica.infrastructure.mongo_projection import _MongoTransaction

MEDICO = "55555555-5555-4555-8555-555555555555"
MEDICO_MAYUSCULAS = MEDICO.upper()
BLOQUES = [{"dia_semana": 0, "inicio": "08:00:00", "fin": "10:00:00"}]


class _Collection:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        self._fail_for = fail_for or set()

    def update_one(self, filtro, cambios, upsert=False):
        self.calls.append((filtro, cambios, {"upsert": upsert}))
        if filtro["_id"] in self._fail_for:
            raise DuplicateKeyError("stored document is newer")


class _Database:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.collections: dict[str, _Collection] = {}
        self._fail_for = fail_for

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(self._fail_for))


def test_key_is_the_canonical_doctor_and_the_week() -> None:
    assert horario_key(MEDICO_MAYUSCULAS, "2026-11-30") == f"{MEDICO}:2026-11-30"


def test_event_writes_the_week_only_over_a_not_newer_version() -> None:
    database = _Database()
    tx = _MongoTransaction(database, version=7)

    tx.upsert_horario_version({"medico_id": MEDICO, "vigente_desde": "2026-11-30"}, BLOQUES, 7)

    filtro, cambios, opciones = database["horarios_trabajo"].calls[0]
    assert filtro == {"_id": f"{MEDICO}:2026-11-30", "_v": {"$lte": 7}}
    assert cambios["$set"]["bloques"] == BLOQUES
    assert cambios["$set"]["_v"] == 7
    assert cambios["$set"]["medico_id"] == MEDICO
    assert opciones == {"upsert": True}


def test_event_ignores_a_newer_stored_week() -> None:
    database = _Database(fail_for={f"{MEDICO}:2026-11-30"})
    tx = _MongoTransaction(database, version=3)

    tx.upsert_horario_version({"medico_id": MEDICO, "vigente_desde": "2026-11-30"}, BLOQUES, 3)

    assert len(database["horarios_trabajo"].calls) == 1


def test_snapshot_writes_every_week_with_the_same_guard() -> None:
    snapshot = build_declared_hours_snapshot(
        [
            (MEDICO, date(2026, 11, 30), 0, time(8, 0), time(10, 0), 5),
            (MEDICO, date(2026, 12, 7), None, None, None, 5),
        ]
    )
    database = _Database()

    upsert_declared_hours_mongo(snapshot, database)

    calls = database["horarios_trabajo"].calls
    assert [c[0] for c in calls] == [
        {"_id": f"{MEDICO}:2026-11-30", "_v": {"$lte": 5}},
        {"_id": f"{MEDICO}:2026-12-07", "_v": {"$lte": 5}},
    ]
    assert calls[1][1]["$set"]["bloques"] == []


def test_snapshot_skips_a_week_stored_newer_and_keeps_going() -> None:
    snapshot = build_declared_hours_snapshot(
        [
            (MEDICO, date(2026, 11, 30), 0, time(8, 0), time(10, 0), 5),
            (MEDICO, date(2026, 12, 7), 0, time(8, 0), time(10, 0), 5),
        ]
    )
    database = _Database(fail_for={f"{MEDICO}:2026-11-30"})

    upsert_declared_hours_mongo(snapshot, database)

    assert len(database["horarios_trabajo"].calls) == 2

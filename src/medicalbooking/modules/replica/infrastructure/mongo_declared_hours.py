"""Declared work hours in MongoDB Atlas: the catalog snapshot (replica-work-hours S3).

One document per doctor and week. A week is written only over a version that is not older, the
same guard the outbox event uses (mongo_projection). A duplicate-key error means the stored week
is newer: it is kept and the snapshot continues with the next week.
"""

from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from medicalbooking.modules.replica.application.declared_hours_sync import (
    DeclaredHoursSnapshot,
    horario_key,
)

HORARIOS = "horarios_trabajo"


def upsert_declared_hours_mongo(snapshot: DeclaredHoursSnapshot, database: Any) -> None:
    for week in snapshot.weeks:
        key = horario_key(week.medico_id, week.vigente_desde)
        try:
            database[HORARIOS].update_one(
                {"_id": key, "_v": {"$lte": week.version}},
                {
                    "$set": {
                        "medico_id": week.medico_id,
                        "vigente_desde": week.vigente_desde,
                        "bloques": list(week.bloques),
                        "_v": week.version,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            continue

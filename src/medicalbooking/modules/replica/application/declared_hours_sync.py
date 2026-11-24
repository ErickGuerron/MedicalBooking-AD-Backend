"""Snapshot of the declared work hours for the replica (replica-work-hours S3).

Each row of the snapshot is one doctor and one week. Its version is the outbox version the source
had reached for that doctor, so the replica applies the snapshot with the same guard as the event:
a row is replaced only by a newer version. Snapshot and events never reverse each other.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Protocol

_UPSERT = (
    "INSERT INTO replica.horarios_trabajo_version (medico_id, vigente_desde, bloques, version) "
    "VALUES (%s, %s, %s::jsonb, %s) "
    "ON CONFLICT (medico_id, vigente_desde) DO UPDATE SET "
    "bloques = excluded.bloques, version = excluded.version "
    "WHERE replica.horarios_trabajo_version.version < excluded.version"
)


def horario_key(medico_id: str, vigente_desde: str) -> str:
    """Document key of one doctor and one week in MongoDB: "<canonical medico>:<YYYY-MM-DD>"."""
    return f"{uuid.UUID(str(medico_id))}:{vigente_desde}"


@dataclass(frozen=True)
class DeclaredWeek:
    medico_id: str
    vigente_desde: str
    bloques: tuple[dict[str, str | int], ...]
    version: int


@dataclass(frozen=True)
class DeclaredHoursSnapshot:
    weeks: tuple[DeclaredWeek, ...]


class _Executor(Protocol):
    def execute(self, sql: str, params: Any = None) -> object: ...


class DeclaredHoursTarget(Protocol):
    def transaction(self) -> AbstractContextManager[_Executor]: ...


def build_declared_hours_snapshot(
    rows: Iterable[tuple[str, date, int | None, time | None, time | None, int]],
) -> DeclaredHoursSnapshot:
    """Group (medico, semana, día, inicio, fin, versión) rows into weeks.

    A week with no blocks arrives as one row with día, inicio and fin None: it is kept, because
    a week emptied on purpose must replace the previous blocks on the replica.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for medico_id, vigente_desde, dia, inicio, fin, version in rows:
        key = (medico_id, vigente_desde.isoformat())
        week = grouped.setdefault(key, {"version": version, "bloques": []})
        if dia is not None:
            week["bloques"].append((dia, inicio.isoformat(), fin.isoformat()))
    weeks = []
    for (medico_id, vigente), week in sorted(grouped.items()):
        blocks = tuple(
            {"dia_semana": dia, "inicio": inicio, "fin": fin}
            for dia, inicio, fin in sorted(week["bloques"])
        )
        weeks.append(DeclaredWeek(medico_id, vigente, blocks, week["version"]))
    return DeclaredHoursSnapshot(weeks=tuple(weeks))


def upsert_declared_hours(snapshot: DeclaredHoursSnapshot, target: DeclaredHoursTarget) -> None:
    """Apply every week in one transaction. A failed write leaves the replica unchanged."""
    with target.transaction() as conn:
        for week in snapshot.weeks:
            conn.execute(
                _UPSERT,
                (week.medico_id, week.vigente_desde, json.dumps(list(week.bloques)), week.version),
            )

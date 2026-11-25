"""The week in force per doctor, for the outage read (replica-work-hours S4).

The replica keeps every declared week. A doctor's schedule on a day is the most recent week
that starts on or before that day. Doctors with no week in force are left out.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any


def vigente_por_medico(
    rows: Iterable[tuple[str, str, str, date, list[dict[str, Any]]]], on: date
) -> list[dict[str, Any]]:
    """rows: (medico_id, nombre, apellido, vigente_desde, bloques) for every declared week."""
    latest: dict[str, tuple[date, str, str, list[dict[str, Any]]]] = {}
    for medico_id, nombre, apellido, vigente_desde, bloques in rows:
        if vigente_desde > on:
            continue
        current = latest.get(medico_id)
        if current is None or vigente_desde > current[0]:
            latest[medico_id] = (vigente_desde, nombre, apellido, bloques)
    vigentes = [
        {
            "medico_id": medico_id,
            "nombre": nombre,
            "apellido": apellido,
            "vigente_desde": vigente_desde.isoformat(),
            "bloques": bloques,
        }
        for medico_id, (vigente_desde, nombre, apellido, bloques) in latest.items()
    ]
    return sorted(vigentes, key=lambda item: (item["apellido"], item["nombre"], item["medico_id"]))

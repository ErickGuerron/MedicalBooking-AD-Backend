"""Read appointments changed since a watermark, as the same snapshot the outbox emits.

Writes made outside the API (external SQL, or writes while the replicas were down) leave no outbox
event, so the watermark sync reads the rows directly. The snapshot keys match the
``cita.actualizada`` payload built by scheduling.infrastructure.repositories.emit_cita_snapshot,
so the replica projection applies both paths the same way.

The row version is ``citas.version_fila`` (rowversion), read as BIGINT. It only moves forward
on insert and update; hard deletes are not visible here (phase 2).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class CitaChange:
    version: int
    cita: dict[str, Any]


_CHANGED_CITAS = text(
    "SELECT TOP (:limite) CAST(c.version_fila AS BIGINT) AS version, "
    "c.id, c.paciente_id, c.servicio_id, c.estado, c.reservada_en, c.confirmada_en, "
    "c.cancelada_en, c.motivo_cancelacion_codigo, c.motivo_cancelacion_texto, "
    "h.inicio_utc, h.fin_utc "
    "FROM citas AS c JOIN horarios_disponibles AS h ON h.id = c.horario_id "
    "WHERE CAST(c.version_fila AS BIGINT) > :desde "
    "ORDER BY CAST(c.version_fila AS BIGINT) ASC"
)


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the replica keys are lower case."""
    return str(uuid.UUID(str(value)))


def _iso(moment: Any) -> str | None:
    return moment.isoformat() if moment is not None else None


def cita_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map one joined row to the ``cita`` object of the ``cita.actualizada`` payload."""
    return {
        "id": _canonical(row["id"]),
        "paciente_id": _canonical(row["paciente_id"]),
        "servicio_id": _canonical(row["servicio_id"]),
        "estado": row["estado"],
        "reservada_en": _iso(row["reservada_en"]),
        "confirmada_en": _iso(row["confirmada_en"]),
        "cancelada_en": _iso(row["cancelada_en"]),
        "motivo_cancelacion_codigo": row["motivo_cancelacion_codigo"],
        "motivo_cancelacion_texto": row["motivo_cancelacion_texto"],
        "inicio": _iso(row["inicio_utc"]),
        "fin": _iso(row["fin_utc"]),
    }


class SqlChangedCitasReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def changes_since(self, watermark: int, limit: int) -> list[CitaChange]:
        """Rows with a version above ``watermark``, oldest first, at most ``limit`` of them."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._engine.connect() as conn:
            rows = (
                conn.execute(_CHANGED_CITAS, {"desde": watermark, "limite": limit}).mappings().all()
            )
        return [CitaChange(version=int(row["version"]), cita=cita_snapshot(row)) for row in rows]

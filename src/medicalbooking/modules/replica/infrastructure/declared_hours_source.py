"""Read the declared work hours from SQL Server for the replica snapshot.

The version of each doctor is the highest outbox version written for that doctor, read in the
same statement as the blocks. The replica guard then drops any snapshot older than an event.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from medicalbooking.modules.replica.application.declared_hours_sync import (
    DeclaredHoursSnapshot,
    build_declared_hours_snapshot,
)

_ROWS = text(
    "SELECT LOWER(CAST(v.medico_id AS NVARCHAR(36))) AS medico_id, v.vigente_desde, "
    "b.dia_semana, b.inicio, b.fin, "
    "(SELECT ISNULL(MAX(o.version_agregado), 0) FROM eventos_outbox AS o "
    " WHERE o.agregado_tipo = 'medico_horario' "
    " AND o.agregado_id = LOWER(CAST(v.medico_id AS NVARCHAR(36)))) AS version "
    "FROM horarios_trabajo_version AS v "
    "LEFT JOIN horarios_trabajo_medico AS b ON b.version_id = v.id"
)


def read_declared_hours(engine: Engine) -> DeclaredHoursSnapshot:
    with engine.connect() as conn:
        rows = conn.execute(_ROWS).all()
    return build_declared_hours_snapshot(
        (row.medico_id, row.vigente_desde, row.dia_semana, row.inicio, row.fin, row.version)
        for row in rows
    )

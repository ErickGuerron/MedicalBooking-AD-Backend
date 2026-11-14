"""Outbox events for tratamientos and seguimientos (treatments S11).

Each helper appends one event on the caller's SQL Server connection, inside the transaction of the
write it describes, so the event commits or rolls back with that write. The payloads are the
replicated shape: no audit fields and no user ids. The aggregate is the treatment, so a follow-up
takes the next version of its treatment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from medicalbooking.modules.treatments.application.ports import FollowUp, Treatment
from medicalbooking.shared.infrastructure.outbox import append_outbox

AGGREGATE = "tratamiento"
EVENT_TREATMENT_CREATED = "tratamiento.creado"
EVENT_TREATMENT_UPDATED = "tratamiento.actualizado"
EVENT_FOLLOWUP_REGISTERED = "seguimiento.registrado"


def treatment_snapshot(treatment: Treatment) -> dict[str, Any]:
    return {
        "id": treatment.id,
        "cita_id": treatment.cita_id,
        "paciente_id": treatment.paciente_id,
        "medico_id": treatment.medico_id,
        "descripcion": treatment.descripcion,
        "estado": treatment.estado,
        "fecha_inicio": treatment.fecha_inicio.isoformat(),
        "fecha_fin": treatment.fecha_fin.isoformat() if treatment.fecha_fin else None,
    }


def followup_snapshot(followup: FollowUp, secuencia: int) -> dict[str, Any]:
    return {
        "id": followup.id,
        "tratamiento_id": followup.tratamiento_id,
        "secuencia": secuencia,
        "nota": followup.nota,
        "creado_en": followup.registrado_en.isoformat(),
    }


def emit_treatment_event(conn: Connection, event: str, treatment: Treatment, now: datetime) -> None:
    append_outbox(
        conn,
        AGGREGATE,
        treatment.id,
        event,
        {"tratamiento": treatment_snapshot(treatment)},
        now,
    )


def emit_followup_event(
    conn: Connection, followup: FollowUp, secuencia: int, now: datetime
) -> None:
    append_outbox(
        conn,
        AGGREGATE,
        followup.tratamiento_id,
        EVENT_FOLLOWUP_REGISTERED,
        {"seguimiento": followup_snapshot(followup, secuencia)},
        now,
    )

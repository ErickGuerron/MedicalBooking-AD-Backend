"""HTTP adapter for scheduling: /api/v1/availability and /api/v1/appointments."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AppointmentSummary,
    AvailableSlot,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.domain.policies import DEFAULT_PAGE_SIZE
from medicalbooking.shared.domain.errors import ValidationFailed

scheduling_bp = Blueprint("scheduling", __name__, url_prefix="/api/v1")
_INTEGER_PARAM = re.compile(r"-?[0-9]+")


class BookAppointmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horario_id: uuid.UUID
    servicio_id: uuid.UUID
    motivo: str | None = Field(default=None, max_length=500)


def _service() -> SchedulingService:
    return current_app.extensions["medicalbooking.scheduling"]


def _instant(name: str) -> datetime:
    raw = request.args.get(name, "")
    if not raw:
        raise ValidationFailed(f"'{name}' is required")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationFailed(f"'{name}' must be an ISO-8601 timestamp") from None
    if moment.tzinfo is None:
        raise ValidationFailed(f"'{name}' must include a timezone offset")
    return moment


def _body() -> BookAppointmentRequest:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return BookAppointmentRequest.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _int_query(name: str, default: int) -> int:
    """Read an optional integer query parameter. Range rules belong to the use case."""
    if name not in request.args:
        return default
    raw = request.args[name]
    if not _INTEGER_PARAM.fullmatch(raw):
        raise ValidationFailed(f"'{name}' must be an integer")
    return int(raw)


def _summary_json(appointment: AppointmentSummary) -> dict[str, Any]:
    return {
        "id": appointment.id,
        "estado": appointment.estado,
        "inicio": appointment.inicio_utc.isoformat(),
        "fin": appointment.fin_utc.isoformat(),
    }


def _slot_json(slot: AvailableSlot) -> dict[str, Any]:
    return {
        "horario_id": slot.horario_id,
        "medico_id": slot.medico_id,
        "inicio_utc": slot.inicio_utc.isoformat(),
        "fin_utc": slot.fin_utc.isoformat(),
    }


def _appointment_json(appointment: Appointment) -> dict[str, Any]:
    expires = appointment.expiracion_reserva
    return {
        "id": appointment.id,
        "estado": appointment.estado,
        "horario_id": appointment.horario_id,
        "servicio_id": appointment.servicio_id,
        "motivo": appointment.motivo,
        "expiracion_reserva": expires.isoformat() if expires else None,
    }


@scheduling_bp.get("/availability")
@authenticated
def availability() -> tuple[Response, int]:
    medico = request.args.get("medico_id")
    slots = _service().list_availability(
        g.actor,
        _instant("desde"),
        _instant("hasta"),
        medico_id=str(uuid.UUID(medico)) if medico else None,
    )
    return jsonify(items=[_slot_json(slot) for slot in slots]), 200


@scheduling_bp.post("/appointments")
@authenticated
def book() -> tuple[Response, int]:
    body = _body()
    result = _service().book(
        g.actor,
        idempotency_key=request.headers.get("Idempotency-Key", ""),
        horario_id=str(body.horario_id),
        servicio_id=str(body.servicio_id),
        motivo=body.motivo,
    )
    # 201 for a new reservation; 200 when an identical retry returns the original one.
    status = 200 if result.replayed else 201
    return jsonify(_appointment_json(result.appointment)), status


@scheduling_bp.post("/appointments/<uuid:appointment_id>/cancel")
@authenticated
def cancel(appointment_id: uuid.UUID) -> tuple[Response, int]:
    appointment = _service().cancel(g.actor, str(appointment_id))
    return jsonify(_appointment_json(appointment)), 200


@scheduling_bp.get("/appointments")
@authenticated
def list_appointments() -> tuple[Response, int]:
    items = _service().list_appointments(
        g.actor,
        estado=request.args.get("estado"),
        limit=_int_query("limit", DEFAULT_PAGE_SIZE),
        offset=_int_query("offset", 0),
    )
    return jsonify(items=[_summary_json(item) for item in items]), 200

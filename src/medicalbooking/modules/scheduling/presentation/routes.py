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


class CancelAppointmentRequest(BaseModel):
    """Optional body. The reason is required for a doctor and ignored for a patient; its
    values are checked by the use case, so a patient's body is only checked for shape."""

    model_config = ConfigDict(extra="forbid")

    motivo_codigo: str | None = None
    motivo_texto: str | None = None


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


def _cancel_body() -> CancelAppointmentRequest:
    """A JSON object is checked for unknown fields. No body, or any other body, carries no
    reason fields, as before this endpoint read a body at all."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return CancelAppointmentRequest()
    try:
        return CancelAppointmentRequest.model_validate(data)
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
    body = _cancel_body()
    appointment = _service().cancel(
        g.actor,
        str(appointment_id),
        motivo_codigo=body.motivo_codigo,
        motivo_texto=body.motivo_texto,
    )
    # The cancellation is committed. Hooks run afterwards and must not change its answer.
    hook = current_app.extensions.get("medicalbooking.after_appointment_cancelled")
    if hook is not None:
        hook(g.actor, str(appointment_id))
    return jsonify(_appointment_json(appointment)), 200


@scheduling_bp.post("/appointments/<uuid:appointment_id>/cancellation-approval")
@authenticated
def approve_cancellation(appointment_id: uuid.UUID) -> tuple[Response, int]:
    """An admin approves one doctor cancellation beyond the quota or inside the notice window."""
    data = request.get_json(silent=True)
    motivo = data.get("motivo") if isinstance(data, dict) else None
    _service().approve_doctor_cancellation(
        g.actor,
        str(appointment_id),
        motivo=motivo if isinstance(motivo, str) else None,
    )
    return jsonify(cita_id=str(appointment_id), aprobada=True), 201


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

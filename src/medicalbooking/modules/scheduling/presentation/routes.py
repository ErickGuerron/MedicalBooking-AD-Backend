"""HTTP adapter for scheduling: availability, appointments, doctor hours and slot generation."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.scheduling.application.office_admin import OfficeAdminService
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    AppointmentSummary,
    AvailableSlot,
    ConsultorioOwned,
    SedeDetail,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.application.work_hours import WorkHoursService
from medicalbooking.modules.scheduling.domain.clinic_hours import WorkBlock
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
    """Optional body. A doctor's reason (code and text) is required; a patient may send only
    motivo_texto, optional, and any patient motivo_codigo is ignored. Values are checked by the
    use case, so the body is only checked for shape here."""

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


def _validate(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValidationFailed("Dates must be real calendar days in YYYY-MM-DD format") from error


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


def _optional_uuid(name: str) -> str | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError as error:
        raise ValidationFailed(f"'{name}' must be a UUID") from error


def _int_query(name: str, default: int) -> int:
    """Read an optional integer query parameter. Range rules belong to the use case."""
    if name not in request.args:
        return default
    raw = request.args[name]
    if not _INTEGER_PARAM.fullmatch(raw):
        raise ValidationFailed(f"'{name}' must be an integer")
    return int(raw)


def _positive_int_query(name: str) -> int | None:
    """Optional positive INT query parameter (an SQL Server int identity)."""
    if name not in request.args:
        return None
    value = _int_query(name, 0)
    if not 1 <= value <= 2_147_483_647:
        raise ValidationFailed(f"'{name}' must be a positive integer")
    return value


def _summary_json(appointment: AppointmentSummary) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": appointment.id,
        "estado": appointment.estado,
        "inicio": appointment.inicio_utc.isoformat(),
        "fin": appointment.fin_utc.isoformat(),
    }
    # Only the doctor's agenda listing carries the patient; a patient's own listing never does.
    if appointment.paciente_id is not None:
        item["paciente"] = {"id": appointment.paciente_id, "nombre": appointment.paciente_nombre}
    if appointment.medico_id is not None:
        medico_data: dict[str, Any] = {
            "id": appointment.medico_id,
            "nombre": appointment.medico_nombre,
        }
        if appointment.medico_registro is not None:
            medico_data["registro_profesional"] = appointment.medico_registro
        if appointment.medico_telefono is not None:
            medico_data["telefono"] = appointment.medico_telefono
        item["medico"] = medico_data
    if appointment.tiene_aprobacion:
        item["tiene_aprobacion"] = True
    return item


def _slot_json(slot: AvailableSlot) -> dict[str, Any]:
    item: dict[str, Any] = {
        "horario_id": slot.horario_id,
        "medico_id": slot.medico_id,
        "inicio_utc": slot.inicio_utc.isoformat(),
        "fin_utc": slot.fin_utc.isoformat(),
    }
    if slot.medico_nombre is not None:
        item["medico"] = {"id": slot.medico_id, "nombre": slot.medico_nombre}
    return item


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
    slots = _service().list_availability(
        g.actor,
        _instant("desde"),
        _instant("hasta"),
        medico_id=_optional_uuid("medico_id"),
        especialidad_id=_positive_int_query("especialidad_id"),
        servicio_id=_optional_uuid("servicio_id"),
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


@scheduling_bp.post("/appointments/<uuid:appointment_id>/consultation")
@authenticated
def open_consultation(appointment_id: uuid.UUID) -> tuple[Response, int]:
    """The doctor of the agenda opens the consultation: 201 when created, 200 when it existed."""
    consultation, created = _service().open_consultation(g.actor, str(appointment_id))
    started = consultation.inicio_atencion
    body = {
        "id": consultation.id,
        "cita_id": consultation.cita_id,
        "estado": consultation.estado,
        "inicio_atencion": started.isoformat() if started else None,
        "motivo_consulta": consultation.motivo_consulta,
    }
    return jsonify(body), 201 if created else 200


@scheduling_bp.post("/appointments/<uuid:appointment_id>/consultation/close")
@authenticated
def close_consultation(appointment_id: uuid.UUID) -> tuple[Response, int]:
    """The doctor registers the attention: notes and plan close the consultation (final)."""
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    consultation = _service().close_consultation(
        g.actor,
        str(appointment_id),
        observaciones=data.get("observaciones"),
        plan_tratamiento=data.get("plan_tratamiento"),
    )
    body = {
        "id": consultation.id,
        "cita_id": consultation.cita_id,
        "estado": consultation.estado,
        "inicio_atencion": consultation.inicio_atencion.isoformat()
        if consultation.inicio_atencion
        else None,
        "fin_atencion": consultation.fin_atencion.isoformat()
        if consultation.fin_atencion
        else None,
        "motivo_consulta": consultation.motivo_consulta,
        "observaciones": consultation.observaciones,
        "plan_tratamiento": consultation.plan_tratamiento,
    }
    return jsonify(body), 200


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


@scheduling_bp.get("/appointments/cancellation-approvals")
@authenticated
def list_cancellation_candidates() -> tuple[Response, int]:
    """Cancellation review (administrator): doctor identity only, no patient, no phone."""
    items = _service().list_cancellation_candidates(
        g.actor,
        estado=request.args.get("estado"),
        medico_registro=request.args.get("medico_registro"),
        limit=_int_query("limit", DEFAULT_PAGE_SIZE),
        offset=_int_query("offset", 0),
    )
    return jsonify(items=[_candidate_json(item) for item in items]), 200


def _candidate_json(appointment: AppointmentSummary) -> dict[str, Any]:
    return {
        "id": appointment.id,
        "estado": appointment.estado,
        "inicio": appointment.inicio_utc.isoformat(),
        "fin": appointment.fin_utc.isoformat(),
        "tiene_aprobacion": appointment.tiene_aprobacion,
        "medico": {
            "id": appointment.medico_id,
            "nombre": appointment.medico_nombre,
            "registro_profesional": appointment.medico_registro,
        },
    }


# ---------- doctor work hours and slot generation ----------


class WorkBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dia_semana: int = Field(ge=0, le=6)
    inicio: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    fin: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$")


class WorkHoursRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bloques: list[WorkBlockIn] = Field(max_length=28)


class GenerateSlotsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medico_id: uuid.UUID
    consultorio_id: uuid.UUID
    # The Monday that starts the week to create.
    semana: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class CreateSedeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str
    direccion: str | None = None


class UpdateSedeRequest(BaseModel):
    """Only sent fields change. A null direccion clears it; a null nombre or activo is refused."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = None
    direccion: str | None = None
    activo: bool | None = None


class CreateConsultorioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str


class UpdateConsultorioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = None
    activo: bool | None = None


def _work_hours() -> WorkHoursService:
    return current_app.extensions["medicalbooking.work_hours"]


def _offices() -> OfficeAdminService:
    return current_app.extensions["medicalbooking.offices"]


def _sede_json(sede: SedeDetail) -> dict[str, Any]:
    return {
        "id": sede.id,
        "nombre": sede.nombre,
        "direccion": sede.direccion,
        "activo": sede.activo,
        "consultorios": [
            {"id": c.id, "nombre": c.nombre, "activo": c.activo} for c in sede.consultorios
        ],
    }


def _consultorio_json(consultorio: ConsultorioOwned) -> dict[str, Any]:
    return {
        "id": consultorio.id,
        "sede_id": consultorio.sede_id,
        "nombre": consultorio.nombre,
        "activo": consultorio.activo,
    }


def _hhmm(text: str) -> time:
    hours, minutes = map(int, text.split(":"))
    if hours > 23:
        raise ValidationFailed("Times must be between 00:00 and 23:59")
    return time(hours, minutes)


def _block_json(block: WorkBlock) -> dict[str, Any]:
    return {
        "dia_semana": block.dia_semana,
        "inicio": block.inicio.strftime("%H:%M"),
        "fin": block.fin.strftime("%H:%M"),
    }


def _day_query(name: str) -> date:
    raw = request.args.get(name, "")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise ValidationFailed(f"'{name}' must be a real date in YYYY-MM-DD format") from error


@scheduling_bp.get("/me/horario-trabajo")
@authenticated
def my_work_hours() -> tuple[Response, int]:
    return jsonify(_hours_json(_work_hours().get_my_work_hours(g.actor))), 200


@scheduling_bp.put("/me/horario-trabajo")
@authenticated
def set_my_work_hours() -> tuple[Response, int]:
    body = _validate(WorkHoursRequest)
    blocks = [
        WorkBlock(dia_semana=item.dia_semana, inicio=_hhmm(item.inicio), fin=_hhmm(item.fin))
        for item in body.bloques
    ]
    return jsonify(_hours_json(_work_hours().set_my_work_hours(g.actor, blocks))), 200


def _hours_json(hours: Any) -> dict[str, Any]:
    """The hours in force this week, and from next week on (the ones the doctor edits)."""
    return {
        "vigente_desde": hours.vigente_desde.isoformat(),
        "actual": [_block_json(block) for block in hours.actual],
        "proximo": [_block_json(block) for block in hours.proximo],
    }


def _week_query() -> date:
    return _parse_day(request.args.get("semana", ""))


@scheduling_bp.get("/admin/medicos/<uuid:medico_id>/horario-trabajo")
@authenticated
def doctor_work_hours(medico_id: uuid.UUID) -> tuple[Response, int]:
    return jsonify(_hours_json(_work_hours().doctor_work_hours(g.actor, str(medico_id)))), 200


@scheduling_bp.get("/admin/consultorios")
@authenticated
def list_consultorios() -> tuple[Response, int]:
    offices = _work_hours().list_consultorios(g.actor)
    return jsonify(items=[{"id": o.id, "nombre": o.nombre, "sede": o.sede} for o in offices]), 200


@scheduling_bp.get("/admin/sedes")
@authenticated
def list_sedes() -> tuple[Response, int]:
    sedes = _offices().list_sedes(g.actor)
    return jsonify(items=[_sede_json(s) for s in sedes]), 200


@scheduling_bp.post("/admin/sedes")
@authenticated
def create_sede() -> tuple[Response, int]:
    body = _validate(CreateSedeRequest)
    sede = _offices().create_sede(g.actor, body.nombre, body.direccion)
    return jsonify(_sede_json(sede)), 201


@scheduling_bp.patch("/admin/sedes/<uuid:sede_id>")
@authenticated
def update_sede(sede_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(UpdateSedeRequest)
    sede = _offices().update_sede(g.actor, str(sede_id), body.model_dump(exclude_unset=True))
    return jsonify(_sede_json(sede)), 200


@scheduling_bp.post("/admin/sedes/<uuid:sede_id>/consultorios")
@authenticated
def create_consultorio(sede_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(CreateConsultorioRequest)
    sede = _offices().create_consultorio(g.actor, str(sede_id), body.nombre)
    return jsonify(_sede_json(sede)), 201


@scheduling_bp.patch("/admin/consultorios/<uuid:consultorio_id>")
@authenticated
def update_consultorio(consultorio_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(UpdateConsultorioRequest)
    consultorio = _offices().update_consultorio(
        g.actor, str(consultorio_id), body.model_dump(exclude_unset=True)
    )
    return jsonify(_consultorio_json(consultorio)), 200


@scheduling_bp.get("/admin/horarios/propuesta")
@authenticated
def propose_slots() -> tuple[Response, int]:
    medico = _optional_uuid("medico_id")
    if medico is None:
        raise ValidationFailed("'medico_id' is required")
    proposal = _work_hours().propose_week(g.actor, medico, _week_query())
    items = [
        {
            "inicio_utc": slot.inicio_utc.isoformat(),
            "fin_utc": slot.fin_utc.isoformat(),
            "franja": slot.franja,
        }
        for slot in proposal
    ]
    return jsonify(items=items, total=len(items)), 200


@scheduling_bp.post("/admin/horarios/generar")
@authenticated
def generate_slots() -> tuple[Response, int]:
    body = _validate(GenerateSlotsRequest)
    created, existing = _work_hours().generate_week(
        g.actor,
        str(body.medico_id),
        str(body.consultorio_id),
        _parse_day(body.semana),
    )
    return jsonify(creados=created, existentes=existing), 201

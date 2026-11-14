"""HTTP adapter for treatments (S4-S6): a treatment per appointment and its follow-ups."""

from __future__ import annotations

import uuid
from datetime import UTC, date
from typing import Any, Literal

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.treatments.application.ports import FollowUp, Treatment
from medicalbooking.modules.treatments.application.use_cases import TreatmentService
from medicalbooking.modules.treatments.domain.policies import MAX_DESCRIPCION, MAX_NOTA
from medicalbooking.shared.domain.errors import ValidationFailed

treatments_bp = Blueprint("treatments", __name__, url_prefix="/api/v1")

Estado = Literal["ACTIVO", "FINALIZADO", "SUSPENDIDO"]


class CreateTreatmentRequest(BaseModel):
    # plan_tratamiento is a free-text summary in consultas_medicas (S8): it is never accepted here.
    model_config = ConfigDict(extra="forbid")

    descripcion: str = Field(min_length=1, max_length=MAX_DESCRIPCION)
    fecha_inicio: date
    estado: Estado = "ACTIVO"
    fecha_fin: date | None = None


class UpdateTreatmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descripcion: str | None = Field(default=None, min_length=1, max_length=MAX_DESCRIPCION)
    estado: Estado | None = None
    fecha_fin: date | None = None


class FollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nota: str = Field(min_length=1, max_length=MAX_NOTA)


def _service() -> TreatmentService:
    return current_app.extensions["medicalbooking.treatments"]


def _parse(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    return _validate(model, data)


def _validate(model: type[BaseModel], data: dict[str, Any]) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _iso_utc(moment) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _treatment_json(treatment: Treatment) -> dict[str, Any]:
    return {
        "id": treatment.id,
        "cita_id": treatment.cita_id,
        "descripcion": treatment.descripcion,
        "estado": treatment.estado,
        "fecha_inicio": treatment.fecha_inicio.isoformat(),
        "fecha_fin": None if treatment.fecha_fin is None else treatment.fecha_fin.isoformat(),
    }


def _followup_json(followup: FollowUp) -> dict[str, Any]:
    return {
        "id": followup.id,
        "tratamiento_id": followup.tratamiento_id,
        "nota": followup.nota,
        "registrado_en": _iso_utc(followup.registrado_en),
    }


@treatments_bp.post("/citas/<uuid:cita_id>/tratamientos")
@authenticated
def create_treatment(cita_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(CreateTreatmentRequest)
    treatment = _service().create_treatment(
        g.actor,
        str(cita_id),
        descripcion=body.descripcion,
        fecha_inicio=body.fecha_inicio,
        estado=body.estado,
        fecha_fin=body.fecha_fin,
    )
    return jsonify(_treatment_json(treatment)), 201


@treatments_bp.get("/citas/<uuid:cita_id>/tratamientos")
@authenticated
def list_treatments(cita_id: uuid.UUID) -> tuple[Response, int]:
    rows = _service().list_treatments(g.actor, str(cita_id))
    return jsonify(items=[_treatment_json(row) for row in rows]), 200


@treatments_bp.patch("/tratamientos/<uuid:tratamiento_id>")
@authenticated
def update_treatment(tratamiento_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(UpdateTreatmentRequest)
    treatment = _service().update_treatment(
        g.actor,
        str(tratamiento_id),
        descripcion=body.descripcion,
        estado=body.estado,
        fecha_fin=body.fecha_fin,
    )
    return jsonify(_treatment_json(treatment)), 200


@treatments_bp.post("/tratamientos/<uuid:tratamiento_id>/seguimientos")
@authenticated
def add_followup(tratamiento_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(FollowUpRequest)
    followup = _service().add_followup(g.actor, str(tratamiento_id), body.nota)
    return jsonify(_followup_json(followup)), 201


@treatments_bp.get("/tratamientos/<uuid:tratamiento_id>/seguimientos")
@authenticated
def list_followups(tratamiento_id: uuid.UUID) -> tuple[Response, int]:
    rows = _service().list_followups(g.actor, str(tratamiento_id))
    return jsonify(items=[_followup_json(row) for row in rows]), 200

"""HTTP adapter for the catalog: specialties, doctors and services (/api/v1)."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.catalog.application.ports import Doctor, Service, Specialty
from medicalbooking.modules.catalog.application.use_cases import CatalogService
from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.shared.domain.errors import ValidationFailed

catalog_bp = Blueprint("catalog", __name__, url_prefix="/api/v1")
_INT_ID = re.compile(r"[0-9]{1,9}")
SQL_INT_MAX = 2_147_483_647


class CreateSpecialtyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=100, pattern=r"\S")
    descripcion: str | None = Field(default=None, max_length=500)


class UpdateSpecialtyRequest(BaseModel):
    """Only the fields present are changed. ``activo`` false hides the specialty from listings."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"\S")
    descripcion: str | None = Field(default=None, max_length=500)
    activo: bool | None = None


class SetDoctorSpecialtiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    especialidad_ids: list[int] = Field(max_length=50)


class CreateServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=150, pattern=r"\S")
    duracion_min: int = Field(gt=0, le=1440)
    precio_base: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    moneda: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    especialidad_id: int | None = Field(default=None, ge=1, le=SQL_INT_MAX)


class SetServiceSpecialtyRequest(BaseModel):
    """``especialidad_id`` is required; null clears the link."""

    model_config = ConfigDict(extra="forbid")

    especialidad_id: int | None = Field(ge=1, le=SQL_INT_MAX)


def _service() -> CatalogService:
    return current_app.extensions["medicalbooking.catalog"]


def _parse(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def specialty_json(specialty: Specialty) -> dict[str, Any]:
    return {"id": specialty.id, "nombre": specialty.nombre, "descripcion": specialty.descripcion}


def _doctor_json(doctor: Doctor) -> dict[str, Any]:
    schedule = doctor.horario_atencion
    return {
        "id": doctor.id,
        "nombre": doctor.nombre,
        "especialidades": [{"id": ref.id, "nombre": ref.nombre} for ref in doctor.especialidades],
        "duracion_cita_min": doctor.duracion_cita_min,
        "horario_atencion": {
            "vigente_desde": schedule.vigente_desde.isoformat() if schedule.vigente_desde else None,
            "vigente_hasta": schedule.vigente_hasta.isoformat() if schedule.vigente_hasta else None,
            "dias": [
                {"dia_semana": day.dia_semana, "desde": day.desde, "hasta": day.hasta}
                for day in schedule.dias
            ],
        },
    }


def _catalog_service_json(service: Service) -> dict[str, Any]:
    return {
        "id": service.id,
        "nombre": service.nombre,
        "duracion_min": service.duracion_min,
        "precio_base": str(service.precio_base),
        "moneda": service.moneda,
        "especialidad_id": service.especialidad_id,
    }


@catalog_bp.get("/specialties")
@authenticated
def list_specialties() -> tuple[Response, int]:
    items = _service().list_specialties(g.actor)
    return jsonify(items=[specialty_json(item) for item in items]), 200


@catalog_bp.get("/doctors")
@authenticated
def list_doctors() -> tuple[Response, int]:
    raw = request.args.get("especialidad_id")
    if raw is not None and not (_INT_ID.fullmatch(raw) and 1 <= int(raw) <= SQL_INT_MAX):
        raise ValidationFailed("'especialidad_id' must be a positive integer")
    items = _service().list_doctors(g.actor, int(raw) if raw is not None else None)
    return jsonify(items=[_doctor_json(item) for item in items]), 200


@catalog_bp.get("/services")
@authenticated
def list_services() -> tuple[Response, int]:
    items = _service().list_services(g.actor)
    return jsonify(items=[_catalog_service_json(item) for item in items]), 200


@catalog_bp.post("/specialties")
@authenticated
def create_specialty() -> tuple[Response, int]:
    body = _parse(CreateSpecialtyRequest)
    specialty = _service().create_specialty(
        g.actor, nombre=body.nombre.strip(), descripcion=body.descripcion
    )
    return jsonify(specialty_json(specialty)), 201


@catalog_bp.patch("/specialties/<int:especialidad_id>")
@authenticated
def update_specialty(especialidad_id: int) -> tuple[Response, int]:
    body = _parse(UpdateSpecialtyRequest)
    changes = body.model_dump(exclude_unset=True)
    if any(key in changes and changes[key] is None for key in ("nombre", "activo")):
        raise ValidationFailed("Invalid fields: 'nombre' and 'activo' cannot be null")
    if "nombre" in changes:
        changes["nombre"] = changes["nombre"].strip()
    specialty = _service().update_specialty(g.actor, especialidad_id, changes)
    return jsonify(specialty_json(specialty)), 200


@catalog_bp.put("/doctors/<uuid:medico_id>/specialties")
@authenticated
def set_doctor_specialties(medico_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(SetDoctorSpecialtiesRequest)
    if any(not 1 <= value <= SQL_INT_MAX for value in body.especialidad_ids):
        raise ValidationFailed("Invalid fields: especialidad_ids")
    refs = _service().set_doctor_specialties(g.actor, str(medico_id), body.especialidad_ids)
    return (
        jsonify(
            medico_id=str(medico_id),
            especialidades=[{"id": ref.id, "nombre": ref.nombre} for ref in refs],
        ),
        200,
    )


@catalog_bp.post("/services")
@authenticated
def create_service() -> tuple[Response, int]:
    body = _parse(CreateServiceRequest)
    service = _service().create_service(
        g.actor,
        nombre=body.nombre.strip(),
        duracion_min=body.duracion_min,
        precio_base=body.precio_base,
        moneda=body.moneda,
        especialidad_id=body.especialidad_id,
    )
    return jsonify(_catalog_service_json(service)), 201


@catalog_bp.patch("/services/<uuid:servicio_id>")
@authenticated
def set_service_specialty(servicio_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(SetServiceSpecialtyRequest)
    service = _service().set_service_specialty(g.actor, str(servicio_id), body.especialidad_id)
    return jsonify(_catalog_service_json(service)), 200

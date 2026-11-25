"""HTTP adapter for the clinical history: /api/v1/patients/{id}/historia and vital signs.

Bodies are strict: a string is never read as a number or a boolean, and unknown fields are refused.
Every request is authenticated first; the service then decides what the caller may see or change.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
)

from medicalbooking.modules.history.application.history_service import HistoryService, HistoryView
from medicalbooking.modules.history.application.ports import Entry, VitalSigns
from medicalbooking.modules.history.domain.policies import KIND_BY_PATH, MOTIVO_MAX
from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.shared.domain.errors import ValidationFailed

history_bp = Blueprint("history", __name__, url_prefix="/api/v1")

Severidad = Literal["LEVE", "MODERADA", "GRAVE", "DESCONOCIDA"]
CategoriaAlergia = Literal["MEDICAMENTO", "ALIMENTO", "LATEX", "AMBIENTAL", "OTRA"]
TipoAntecedente = Literal["PERSONAL", "FAMILIAR", "QUIRURGICO"]


class AlergiaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sustancia: StrictStr
    categoria: CategoriaAlergia
    reaccion: StrictStr | None = None
    severidad: Severidad


class AntecedenteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: TipoAntecedente
    descripcion: StrictStr
    parentesco: StrictStr | None = None
    cie11: StrictStr | None = None


class MedicacionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    medicamento: StrictStr
    dosis: StrictStr | None = None
    frecuencia: StrictStr | None = None
    inicio: date | None = None


class ProblemaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descripcion: StrictStr
    cie11: StrictStr | None = None
    inicio: date | None = None


class VacunaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vacuna: StrictStr
    dosis_numero: StrictInt = Field(ge=1, le=10)
    fecha: date
    lote: StrictStr | None = None


class EstadoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_estado: StrictStr
    estado: StrictStr
    motivo: StrictStr | None = Field(default=None, max_length=MOTIVO_MAX)


class SignosVitalesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peso_kg: StrictFloat | StrictInt | None = None
    talla_cm: StrictFloat | StrictInt | None = None
    pa_sistolica: StrictInt | None = None
    pa_diastolica: StrictInt | None = None
    frecuencia_cardiaca: StrictInt | None = None
    temperatura_c: StrictFloat | StrictInt | None = None
    saturacion: StrictInt | None = None


_BODY_BY_KIND: dict[str, type[BaseModel]] = {
    "alergia": AlergiaIn,
    "antecedente": AntecedenteIn,
    "medicacion": MedicacionIn,
    "problema": ProblemaIn,
    "vacuna": VacunaIn,
}


def _service() -> HistoryService:
    return current_app.extensions["medicalbooking.history"]


def _validate(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _kind(path_kind: str) -> str:
    kind = KIND_BY_PATH.get(path_kind)
    if kind is None:
        raise ValidationFailed("Unknown kind of history entry")
    return kind


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def entry_json(entry: Entry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "tipo": entry.kind,
        "paciente_id": entry.paciente_id,
        "estado": entry.estado,
        "origen": entry.origen,
        "verificada": entry.verificada,
        "verificada_por": entry.verificada_por,
        "verificada_en": _iso(entry.verificada_en),
        "registrada_en": _iso(entry.registrada_en),
        **entry.data,
    }


def vitals_json(vitals: VitalSigns) -> dict[str, Any]:
    return {
        "id": vitals.id,
        "paciente_id": vitals.paciente_id,
        "cita_id": vitals.cita_id,
        "corrige_a": vitals.corrige_a,
        "motivo_correccion": vitals.motivo_correccion,
        "registrada_en": _iso(vitals.registrada_en),
        # Decimal columns come back as Decimal: clinical values are numbers, not strings.
        **{
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in vitals.values.items()
        },
    }


def history_json(view: HistoryView) -> dict[str, Any]:
    body: dict[str, Any] = {"paciente_id": view.paciente_id}
    for path, kind in KIND_BY_PATH.items():
        body[path] = [entry_json(entry) for entry in view.entries.get(kind, [])]
    body["signos_vitales"] = [vitals_json(vitals) for vitals in view.vitals]
    return body


@history_bp.get("/patients/<uuid:paciente_id>/historia")
@authenticated
def read_history(paciente_id: uuid.UUID) -> tuple[Response, int]:
    view = _service().read_history(g.actor, str(paciente_id))
    return jsonify(history_json(view)), 200


@history_bp.post("/patients/<uuid:paciente_id>/historia/<path_kind>")
@authenticated
def create_entry(paciente_id: uuid.UUID, path_kind: str) -> tuple[Response, int]:
    kind = _kind(path_kind)
    body = _validate(_BODY_BY_KIND[kind])
    entry = _service().create_entry(
        g.actor, kind, str(paciente_id), body.model_dump(exclude_unset=False)
    )
    return jsonify(entry_json(entry)), 201


@history_bp.post("/patients/<uuid:paciente_id>/historia/<path_kind>/<uuid:entry_id>/verificar")
@authenticated
def verify_entry(
    paciente_id: uuid.UUID, path_kind: str, entry_id: uuid.UUID
) -> tuple[Response, int]:
    entry = _service().verify_entry(
        g.actor, _kind(path_kind), str(entry_id), paciente_id=str(paciente_id)
    )
    return jsonify(entry_json(entry)), 200


@history_bp.patch("/patients/<uuid:paciente_id>/historia/<path_kind>/<uuid:entry_id>/estado")
@authenticated
def change_estado(
    paciente_id: uuid.UUID, path_kind: str, entry_id: uuid.UUID
) -> tuple[Response, int]:
    body = _validate(EstadoIn)
    entry = _service().change_estado(
        g.actor,
        _kind(path_kind),
        str(entry_id),
        paciente_id=str(paciente_id),
        expected=body.expected_estado,
        target=body.estado,
        motivo=body.motivo,
    )
    return jsonify(entry_json(entry)), 200


class CorreccionVitalesIn(SignosVitalesIn):
    motivo: StrictStr = Field(min_length=1, max_length=MOTIVO_MAX)


@history_bp.post("/signos-vitales/<uuid:vitals_id>/correccion")
@authenticated
def correct_vitals(vitals_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(CorreccionVitalesIn)
    vitals = _service().correct_vitals(
        g.actor, str(vitals_id), body.model_dump(exclude={"motivo"}), body.motivo
    )
    return jsonify(vitals_json(vitals)), 201


@history_bp.post("/citas/<uuid:cita_id>/signos-vitales")
@authenticated
def record_vitals(cita_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(SignosVitalesIn)
    vitals = _service().record_vitals(g.actor, str(cita_id), body.model_dump())
    return jsonify(vitals_json(vitals)), 201

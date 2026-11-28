"""HTTP adapter for the clinical history: histories, the event log, vital signs and catalogs.

Bodies are strict: a string is never read as a number or a boolean, and unknown fields are refused.
Every request is authenticated first; the service then decides what the caller may see or change.
Dates in the query string are ISO days (YYYY-MM-DD), inclusive on both sides.
"""

from __future__ import annotations

import json
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

from medicalbooking.modules.history.application.history_service import (
    CATALOG_CIE11,
    CATALOG_SUSTANCIAS,
    CATALOG_VACUNAS,
    EventPage,
    HistoryService,
    HistoryView,
)
from medicalbooking.modules.history.application.ports import CatalogItem, Entry, VitalSigns
from medicalbooking.modules.history.domain.policies import (
    KIND_BY_PATH,
    MOTIVO_MAX,
    DateRange,
)
from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.shared.domain.errors import ValidationFailed

history_bp = Blueprint("history", __name__, url_prefix="/api/v1")

Severidad = Literal["LEVE", "MODERADA", "GRAVE", "DESCONOCIDA"]
TipoAntecedente = Literal["PERSONAL", "FAMILIAR", "QUIRURGICO"]
_CATALOG_BY_PATH = {
    "cie11": CATALOG_CIE11,
    "vacunas": CATALOG_VACUNAS,
    "sustancias-alergia": CATALOG_SUSTANCIAS,
}


class AlergiaIn(BaseModel):
    """The allergen is a catalog code; its name and category come from the catalog."""

    model_config = ConfigDict(extra="forbid")

    sustancia_codigo: StrictStr
    severidad: Severidad
    reaccion: StrictStr | None = None


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
    """The vaccine is a catalog code; its name comes from the catalog."""

    model_config = ConfigDict(extra="forbid")

    vacuna_codigo: StrictStr
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


class CorreccionVitalesIn(SignosVitalesIn):
    motivo: StrictStr = Field(min_length=1, max_length=MOTIVO_MAX)


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


def _day(name: str) -> date | None:
    """An optional ISO day from the query string. Anything else is a validation error."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationFailed(f"'{name}' must be a date as YYYY-MM-DD") from None


def _int_arg(name: str) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    if not raw.lstrip("-").isdigit():
        raise ValidationFailed(f"'{name}' must be an integer")
    return int(raw)


def _range() -> DateRange:
    from medicalbooking.modules.history.domain.policies import validate_rango

    return validate_rango(_day("desde"), _day("hasta"))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _number(value: Any) -> Any:
    # Decimal columns arrive as Decimal: clinical values are numbers, not strings.
    return float(value) if isinstance(value, Decimal) else value


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
        **{key: _number(value) for key, value in vitals.values.items()},
    }


def history_json(view: HistoryView) -> dict[str, Any]:
    body: dict[str, Any] = {"paciente_id": view.paciente_id}
    for path, kind in KIND_BY_PATH.items():
        body[path] = [entry_json(entry) for entry in view.entries.get(kind, [])]
    body["signos_vitales"] = [vitals_json(vitals) for vitals in view.vitals]
    return body


def event_json(row: Any, incluye_actor: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row.id,
        "entidad": row.entidad,
        "entidad_id": row.entidad_id,
        "accion": row.accion,
        "motivo": row.motivo,
        "antes": json.loads(row.antes) if row.antes else None,
        "despues": json.loads(row.despues) if row.despues else None,
        "ocurrido_en": _iso(row.ocurrido_en),
    }
    if incluye_actor:
        item["actor_usuario_id"] = row.actor_usuario_id
    return item


def event_page_json(page: EventPage) -> dict[str, Any]:
    return {
        "items": [event_json(row, page.incluye_actor) for row in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "incluye_actor": page.incluye_actor,
    }


# ---------- patient histories ----------


@history_bp.get("/patients/<uuid:paciente_id>/historia")
@authenticated
def read_history(paciente_id: uuid.UUID) -> tuple[Response, int]:
    view = _service().read_history(g.actor, str(paciente_id), _range())
    return jsonify(history_json(view)), 200


@history_bp.get("/patients/<uuid:paciente_id>/historia/eventos")
@authenticated
def read_events(paciente_id: uuid.UUID) -> tuple[Response, int]:
    page = _service().list_events(
        g.actor,
        str(paciente_id),
        entidad=request.args.get("entidad") or None,
        accion=request.args.get("accion") or None,
        rango=_range(),
        limit=_int_arg("limit"),
        offset=_int_arg("offset"),
    )
    return jsonify(event_page_json(page)), 200


@history_bp.get("/me/historia")
@authenticated
def read_own_history() -> tuple[Response, int]:
    service = _service()
    view = service.read_history(g.actor, service.own_patient_id(g.actor), _range())
    return jsonify(history_json(view)), 200


@history_bp.get("/me/historia/eventos")
@authenticated
def read_own_events() -> tuple[Response, int]:
    service = _service()
    page = service.list_events(
        g.actor,
        service.own_patient_id(g.actor),
        entidad=request.args.get("entidad") or None,
        accion=request.args.get("accion") or None,
        rango=_range(),
        limit=_int_arg("limit"),
        offset=_int_arg("offset"),
    )
    return jsonify(event_page_json(page)), 200


@history_bp.post("/patients/<uuid:paciente_id>/historia/<path_kind>")
@authenticated
def create_entry(paciente_id: uuid.UUID, path_kind: str) -> tuple[Response, int]:
    kind = _kind(path_kind)
    body = _validate(_BODY_BY_KIND[kind])
    entry = _service().create_entry(g.actor, kind, str(paciente_id), body.model_dump())
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


# ---------- vital signs ----------


@history_bp.post("/citas/<uuid:cita_id>/signos-vitales")
@authenticated
def record_vitals(cita_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(SignosVitalesIn)
    vitals = _service().record_vitals(g.actor, str(cita_id), body.model_dump())
    return jsonify(vitals_json(vitals)), 201


@history_bp.post("/signos-vitales/<uuid:vitals_id>/correccion")
@authenticated
def correct_vitals(vitals_id: uuid.UUID) -> tuple[Response, int]:
    body = _validate(CorreccionVitalesIn)
    vitals = _service().correct_vitals(
        g.actor, str(vitals_id), body.model_dump(exclude={"motivo"}), body.motivo
    )
    return jsonify(vitals_json(vitals)), 201


# ---------- catalogs ----------


def catalog_json(item: CatalogItem) -> dict[str, Any]:
    body: dict[str, Any] = {"codigo": item.codigo, "nombre": item.nombre}
    if item.categoria is not None:
        body["categoria"] = item.categoria
    return body


@history_bp.get("/catalogos/<catalog_path>")
@authenticated
def search_catalog(catalog_path: str) -> tuple[Response, int]:
    catalog = _CATALOG_BY_PATH.get(catalog_path)
    if catalog is None:
        raise ValidationFailed("'catalog' is not a known catalog")
    items = _service().search_catalog(g.actor, catalog, request.args.get("q"), _int_arg("limit"))
    return jsonify(items=[catalog_json(item) for item in items]), 200

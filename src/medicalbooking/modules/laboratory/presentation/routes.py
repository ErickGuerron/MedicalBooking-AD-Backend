"""HTTP adapter for laboratory (E3): orders, results, medical review and PDF documents."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any, Literal

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.laboratory.application.ports import (
    LabOrder,
    LabOrderSummary,
    LabResult,
    ResultEntry,
    StoredDocument,
)
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.modules.laboratory.domain.policies import MAX_PDF_BYTES, MAX_TESTS_PER_ORDER
from medicalbooking.shared.domain.errors import ValidationFailed

lab_bp = Blueprint("laboratory", __name__, url_prefix="/api/v1")


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paciente_id: uuid.UUID
    prioridad: str = Field(default="NORMAL", max_length=20)
    observaciones: str | None = Field(default=None, max_length=1000)
    tipo_prueba_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_TESTS_PER_ORDER)


class ResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre_analito: str = Field(min_length=1, max_length=150)
    valor_texto: str | None = Field(default=None, max_length=300)
    valor_numerico: float | None = None
    unidad: str | None = Field(default=None, max_length=40)
    # Optional (E5): with an analyte the result is classified against its validated reference.
    analito_id: uuid.UUID | None = None
    metodo_analitico: str | None = Field(default=None, max_length=150)
    tipo_muestra: str | None = Field(default=None, max_length=80)
    # Optional (E5.1): e.g. "AYUNO". Unknown (absent) context never satisfies a context-bound rule.
    contexto_muestra: str | None = Field(default=None, max_length=100)
    # Optional (S18): pregnancy state at sample time. Absent means unknown.
    estado_embarazo: Literal["NO", "T1", "T2", "T3"] | None = None


class RecordResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detalle_id: uuid.UUID
    resultados: list[ResultItem] = Field(min_length=1, max_length=50)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(min_length=1, max_length=4000)
    recomendaciones: str | None = Field(default=None, max_length=4000)


class ListOrdersQuery(BaseModel):
    """Query string of GET /lab/orders. Unknown parameters are ignored, invalid ones are 422."""

    estado: Literal["SOLICITADA", "EN_PROCESO", "COMPLETADA", "CANCELADA"] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


def _service() -> LabService:
    return current_app.extensions["medicalbooking.laboratory"]


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


def _order_json(order: LabOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "paciente_id": order.paciente_id,
        "prioridad": order.prioridad,
        "estado": order.estado,
        "observaciones": order.observaciones,
        "pruebas": [
            {"detalle_id": d.id, "tipo_prueba_id": d.tipo_prueba_id, "estado": d.estado}
            for d in order.details
        ],
    }


def _summary_json(order: LabOrderSummary) -> dict[str, Any]:
    moment = order.fecha if order.fecha.tzinfo is not None else order.fecha.replace(tzinfo=UTC)
    return {
        "id": order.id,
        "paciente_id": order.paciente_id,
        "estado": order.estado,
        "fecha": moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _result_json(result: LabResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "detalle_id": result.detalle_id,
        "nombre_analito": result.nombre_analito,
        "valor_texto": result.valor_texto,
        "valor_numerico": result.valor_numerico,
        "unidad": result.unidad,
    }


def _document_json(document: StoredDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "orden_id": document.orden_id,
        "nombre_archivo": document.nombre_archivo,
        "tipo_mime": document.tipo_mime,
        "tamano_bytes": document.tamano_bytes,
        "hash_sha256": document.hash_sha256,
    }


@lab_bp.post("/lab/orders")
@authenticated
def create_order() -> tuple[Response, int]:
    body = _parse(CreateOrderRequest)
    order = _service().create_order(
        g.actor,
        paciente_id=str(body.paciente_id),
        prioridad=body.prioridad,
        observaciones=body.observaciones,
        tipo_prueba_ids=[str(item) for item in body.tipo_prueba_ids],
    )
    return jsonify(_order_json(order)), 201


@lab_bp.get("/lab/orders")
@authenticated
def list_orders() -> tuple[Response, int]:
    query = _validate(ListOrdersQuery, request.args.to_dict())
    rows = _service().list_orders(
        g.actor, estado=query.estado, limit=query.limit, offset=query.offset
    )
    return jsonify(items=[_summary_json(row) for row in rows]), 200


@lab_bp.get("/lab/orders/<uuid:order_id>")
@authenticated
def get_order(order_id: uuid.UUID) -> tuple[Response, int]:
    return jsonify(_order_json(_service().get_order(g.actor, str(order_id)))), 200


@lab_bp.post("/lab/orders/<uuid:order_id>/results")
@authenticated
def record_results(order_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(RecordResultsRequest)
    entries = [
        ResultEntry(
            nombre_analito=item.nombre_analito,
            valor_texto=item.valor_texto,
            valor_numerico=item.valor_numerico,
            unidad=item.unidad,
            analito_id=None if item.analito_id is None else str(item.analito_id),
            metodo_analitico=item.metodo_analitico,
            tipo_muestra=item.tipo_muestra,
            contexto_muestra=item.contexto_muestra,
            estado_embarazo=item.estado_embarazo,
        )
        for item in body.resultados
    ]
    order = _service().record_results(g.actor, str(order_id), str(body.detalle_id), entries)
    return jsonify(_order_json(order)), 200


@lab_bp.get("/lab/orders/<uuid:order_id>/results")
@authenticated
def list_results(order_id: uuid.UUID) -> tuple[Response, int]:
    results = _service().list_results(g.actor, str(order_id))
    return jsonify(items=[_result_json(item) for item in results]), 200


@lab_bp.post("/lab/orders/<uuid:order_id>/review")
@authenticated
def review(order_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(ReviewRequest)
    _service().review(
        g.actor,
        str(order_id),
        conclusion=body.conclusion,
        recomendaciones=body.recomendaciones,
    )
    return jsonify(estado="COMPLETADA", orden_id=str(order_id)), 201


@lab_bp.post("/lab/orders/<uuid:order_id>/documents")
@authenticated
def upload_document(order_id: uuid.UUID) -> tuple[Response, int]:
    upload = request.files.get("archivo")
    if upload is None:
        raise ValidationFailed("A 'archivo' field with the PDF is required")
    # Read at most one byte past the limit: an oversized upload never reaches memory whole.
    content = upload.stream.read(MAX_PDF_BYTES + 1)
    tipo = request.form.get("tipo_documento", "INFORME_LABORATORIO")
    document = _service().upload_document(
        g.actor,
        str(order_id),
        content=content,
        mime=upload.mimetype or "",
        filename=upload.filename or "",
        tipo_documento=tipo,
    )
    return jsonify(_document_json(document)), 201


@lab_bp.get("/documents/<uuid:document_id>/download")
@authenticated
def download_document(document_id: uuid.UUID) -> Response:
    document, content = _service().download_document(g.actor, str(document_id))
    # The filename in the header is generated from the id, never copied from the client.
    response = Response(content, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="documento-{document.id}.pdf"'
    response.headers["Cache-Control"] = "no-store"
    return response

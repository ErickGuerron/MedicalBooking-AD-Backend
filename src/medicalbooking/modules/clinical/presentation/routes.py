"""HTTP adapter for clinical knowledge and proposals (E5)."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.clinical.application.clinical_profile import (
    ClinicalProfile,
    PatientProfileService,
)
from medicalbooking.modules.clinical.application.knowledge import KnowledgeService
from medicalbooking.modules.clinical.application.ports import (
    FindingView,
    ProposalView,
    ShadowSnapshotRecord,
)
from medicalbooking.modules.clinical.application.proposals import ProposalService
from medicalbooking.modules.clinical.application.shadow_findings import ShadowFindingsService
from medicalbooking.modules.clinical.domain.evaluation import CriterionSpec
from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.shared.domain.errors import ValidationFailed

clinical_bp = Blueprint("clinical", __name__, url_prefix="/api/v1")


class AnalyteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=150)
    unidad: str | None = Field(default=None, max_length=40)
    tipo_valor: str = Field(default="NUMERICO", max_length=20)


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fuente_id: uuid.UUID
    unidad: str = Field(min_length=1, max_length=40)
    minimo: float | None = None
    maximo: float | None = None
    sexo: str | None = Field(default=None, max_length=30)
    edad_min_dias: int | None = Field(default=None, ge=0)
    edad_max_dias: int | None = Field(default=None, ge=0)
    metodo: str | None = Field(default=None, max_length=150)
    tipo_muestra: str | None = Field(default=None, max_length=80)
    contexto: str | None = Field(default=None, max_length=100)
    fecha_inicio: date
    fecha_fin: date | None = None


class CriterionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analito_id: uuid.UUID
    operador: str = Field(max_length=20)
    umbral: float | None = None
    unidad: str | None = Field(default=None, max_length=40)


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(min_length=2, max_length=80)
    nombre: str = Field(min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, max_length=1500)
    tipo: str = Field(max_length=30)
    expresion_logica: str = Field(max_length=20)
    mensaje: str = Field(min_length=1, max_length=2000)
    prioridad: str = Field(default="INFORMATIVA", max_length=25)
    criterios: list[CriterionRequest] = Field(min_length=1, max_length=10)


class ShadowEditRequest(BaseModel):
    """A physician's action on one shadow finding (S7): CONFIRMAR, DESCARTAR or MODIFICAR."""

    model_config = ConfigDict(extra="forbid")

    accion: str = Field(max_length=20)
    categoria_nueva: str | None = Field(default=None, max_length=60)
    motivo: str | None = Field(default=None, max_length=600)


class ClinicalProfileRequest(BaseModel):
    """Self-reported altitude of residence and smoking (WHO 2024 Tables 4, 5), both mandatory (S19).

    A smoker declares cigarettes per day; a non-smoker omits it.
    """

    model_config = ConfigDict(extra="forbid")

    altitud_residencia_m: int = Field(ge=0, le=8848)
    fumador: bool
    cigarrillos_dia: int | None = Field(default=None, ge=0, le=200)


class ProposalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(max_length=20)
    motivo: str | None = Field(default=None, max_length=2000)
    conclusion_corregida: str | None = Field(default=None, max_length=4000)


def _knowledge() -> KnowledgeService:
    return current_app.extensions["medicalbooking.knowledge"]


def _proposals() -> ProposalService:
    return current_app.extensions["medicalbooking.proposals"]


def _shadow() -> ShadowFindingsService:
    return current_app.extensions["medicalbooking.shadow"]


def _patient_profile() -> PatientProfileService:
    return current_app.extensions["medicalbooking.patient_profile"]


def _plain(value: Any) -> Any:
    """JSON-safe copy: decimals become numbers, dataclass dicts stay as they are."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _parse(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _reference_json(reference: Reference) -> dict[str, Any]:
    return {
        "id": reference.id,
        "version": reference.version,
        "unidad": reference.unidad,
        "minimo": None if reference.minimo is None else float(reference.minimo),
        "maximo": None if reference.maximo is None else float(reference.maximo),
        "sexo": reference.sexo,
        "edad_min_dias": reference.edad_min_dias,
        "edad_max_dias": reference.edad_max_dias,
        "metodo": reference.metodo,
        "tipo_muestra": reference.tipo_muestra,
        "contexto": reference.contexto,
        "fecha_inicio": reference.fecha_inicio.isoformat(),
        "fecha_fin": reference.fecha_fin.isoformat() if reference.fecha_fin else None,
    }


def _finding_json(finding: FindingView) -> dict[str, Any]:
    return {
        "id": finding.id,
        "regla_codigo": finding.rule_code,
        "regla_version": finding.rule_version,
        "tipo": finding.tipo,
        "explicacion": finding.explicacion,
        "estado_revision": finding.estado_revision,
        "evidencia": [
            {
                "resultado_id": resultado_id,
                "valor_observado": None if valor is None else float(valor),
                "unidad_observada": unidad,
            }
            for resultado_id, valor, unidad in finding.evidence
        ],
    }


def _proposal_json(proposal: ProposalView) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "orden_id": proposal.order_id,
        "estado": proposal.estado,
        "version_motor": proposal.version_motor,
        "resumen": proposal.resumen,
        "creado_en": proposal.creado_en.isoformat(),
        "hallazgos": [_finding_json(item) for item in proposal.findings],
        "decision": proposal.decision,
        "conclusion_medica": proposal.conclusion_medica,
        "aviso": "Propuesta preliminar: no es un diagnostico.",
    }


# ---------- knowledge ----------


@clinical_bp.get("/knowledge/analytes")
@authenticated
def list_analytes() -> tuple[Response, int]:
    analytes = _knowledge().list_analytes(g.actor)
    items = [
        {"id": a.id, "nombre": a.nombre, "unidad": a.unidad, "tipo_valor": a.tipo_valor}
        for a in analytes
    ]
    return jsonify(items=items), 200


@clinical_bp.post("/knowledge/analytes")
@authenticated
def create_analyte() -> tuple[Response, int]:
    body = _parse(AnalyteRequest)
    analyte_id = _knowledge().create_analyte(
        g.actor, nombre=body.nombre, unidad=body.unidad, tipo_valor=body.tipo_valor
    )
    return jsonify(id=analyte_id, estado="ACTIVO"), 201


@clinical_bp.get("/knowledge/analytes/<uuid:analyte_id>/references")
@authenticated
def list_references(analyte_id: uuid.UUID) -> tuple[Response, int]:
    raw = request.args.get("fecha")
    try:
        on = date.fromisoformat(raw) if raw else datetime.now(UTC).date()
    except ValueError:
        raise ValidationFailed("'fecha' must be an ISO date") from None
    references = _knowledge().list_validated_references(g.actor, str(analyte_id), on)
    return jsonify(items=[_reference_json(r) for r in references]), 200


@clinical_bp.post("/knowledge/analytes/<uuid:analyte_id>/references")
@authenticated
def create_reference(analyte_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(ReferenceRequest)
    reference_id = _knowledge().create_reference(
        g.actor,
        str(analyte_id),
        fuente_id=str(body.fuente_id),
        unidad=body.unidad,
        minimo=_decimal(body.minimo),
        maximo=_decimal(body.maximo),
        sexo=body.sexo,
        edad_min_dias=body.edad_min_dias,
        edad_max_dias=body.edad_max_dias,
        metodo=body.metodo,
        tipo_muestra=body.tipo_muestra,
        contexto=body.contexto,
        fecha_inicio=body.fecha_inicio,
        fecha_fin=body.fecha_fin,
    )
    return jsonify(id=reference_id, estado="BORRADOR"), 201


@clinical_bp.post("/knowledge/references/<uuid:reference_id>/validate")
@authenticated
def validate_reference(reference_id: uuid.UUID) -> tuple[Response, int]:
    _knowledge().validate_reference(g.actor, str(reference_id))
    return jsonify(id=str(reference_id), estado="VALIDADA"), 200


@clinical_bp.post("/knowledge/rules")
@authenticated
def create_rule() -> tuple[Response, int]:
    body = _parse(RuleRequest)
    criteria = [
        CriterionSpec(
            analito_id=str(item.analito_id),
            operador=item.operador,
            umbral=_decimal(item.umbral),
            unidad=item.unidad,
        )
        for item in body.criterios
    ]
    rule_id = _knowledge().create_rule(
        g.actor,
        codigo=body.codigo,
        nombre=body.nombre,
        descripcion=body.descripcion,
        tipo=body.tipo,
        expresion_logica=body.expresion_logica,
        mensaje=body.mensaje,
        prioridad=body.prioridad,
        criteria=criteria,
    )
    return jsonify(id=rule_id, estado="BORRADOR"), 201


@clinical_bp.post("/knowledge/rules/<uuid:rule_id>/validate")
@authenticated
def validate_rule(rule_id: uuid.UUID) -> tuple[Response, int]:
    _knowledge().validate_rule(g.actor, str(rule_id))
    return jsonify(id=str(rule_id), estado="VALIDADA"), 200


# ---------- proposals ----------


@clinical_bp.post("/lab/orders/<uuid:order_id>/evaluate")
@authenticated
def evaluate(order_id: uuid.UUID) -> tuple[Response, int]:
    proposal = _proposals().evaluate(g.actor, str(order_id))
    return jsonify(_proposal_json(proposal)), 201


@clinical_bp.get("/lab/orders/<uuid:order_id>/proposal")
@authenticated
def get_proposal(order_id: uuid.UUID) -> tuple[Response, int]:
    proposal = _proposals().get_proposal(g.actor, str(order_id))
    return jsonify(_proposal_json(proposal)), 200


@clinical_bp.get("/lab/orders/<uuid:order_id>/sombra")
@authenticated
def get_shadow(order_id: uuid.UUID) -> tuple[Response, int]:
    """Shadow findings (S13): read-only, nothing stored, the decision is not modified."""
    report = _shadow().get(g.actor, str(order_id))
    return jsonify(_plain(asdict(report))), 200


@clinical_bp.put("/me/perfil-clinico")
@authenticated
def save_clinical_profile() -> tuple[Response, int]:
    """The patient's own altitude and smoking, for the WHO 2024 adjustments (S12)."""
    body = _parse(ClinicalProfileRequest)
    profile = ClinicalProfile(
        altitud_residencia_m=body.altitud_residencia_m,
        fumador=body.fumador,
        cigarrillos_dia=body.cigarrillos_dia,
    )
    _patient_profile().save(g.actor, profile)
    return (
        jsonify(
            altitud_residencia_m=body.altitud_residencia_m,
            fumador=body.fumador,
            cigarrillos_dia=body.cigarrillos_dia,
        ),
        200,
    )


@clinical_bp.post("/lab/orders/<uuid:order_id>/sombra/snapshots")
@authenticated
def record_shadow_snapshot(order_id: uuid.UUID) -> tuple[Response, int]:
    """Explicit snapshot of the shadow findings (S9): a new immutable record on every call."""
    snapshot = _shadow().record_snapshot(g.actor, str(order_id))
    return jsonify(_snapshot_json(snapshot)), 201


@clinical_bp.post(
    "/lab/orders/<uuid:order_id>/sombra/snapshots/<uuid:snapshot_id>/hallazgos/<hallazgo>/ediciones"
)
@authenticated
def edit_shadow_finding(
    order_id: uuid.UUID, snapshot_id: uuid.UUID, hallazgo: str
) -> tuple[Response, int]:
    """Physician edit of one finding of a snapshot (S7, S14). Appends a row; never overwrites."""
    body = _parse(ShadowEditRequest)
    edit_id, categoria_guardada = _shadow().edit_finding(
        g.actor,
        str(order_id),
        str(snapshot_id),
        hallazgo,
        accion=body.accion,
        categoria_nueva=body.categoria_nueva,
        motivo=body.motivo,
    )
    return (
        jsonify(
            id=edit_id,
            snapshot_id=str(snapshot_id),
            hallazgo=hallazgo,
            accion=body.accion,
            categoria_guardada=categoria_guardada,
        ),
        201,
    )


def _snapshot_json(snapshot: ShadowSnapshotRecord) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "orden_id": snapshot.orden_id,
        "version_regla": snapshot.version_regla,
        "modo": "SOMBRA",
        "modifica_decision": False,
        "creado_en": snapshot.creado_en.isoformat(),
        "items": [
            {
                "hallazgo": item.hallazgo,
                "categoria": item.categoria,
                "corte_valor": None if item.corte_valor is None else float(item.corte_valor),
                "corte_unidad": item.corte_unidad,
                "fuente": item.fuente,
                "motivo": item.motivo,
                "evidencia": [
                    {
                        "rol": evidence.rol,
                        "resultado_id": evidence.resultado_id,
                        "valor": float(evidence.valor),
                        "unidad": evidence.unidad,
                    }
                    for evidence in item.evidencia
                ],
            }
            for item in snapshot.items
        ],
    }


@clinical_bp.post("/clinical/proposals/<uuid:proposal_id>/reviews")
@authenticated
def review_proposal(proposal_id: uuid.UUID) -> tuple[Response, int]:
    body = _parse(ProposalReviewRequest)
    _proposals().review(
        g.actor,
        str(proposal_id),
        decision=body.decision,
        motivo=body.motivo,
        conclusion_corregida=body.conclusion_corregida,
    )
    return jsonify(id=str(proposal_id), decision=body.decision), 201

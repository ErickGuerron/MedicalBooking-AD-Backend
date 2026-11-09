"""Ports for the clinical knowledge and proposals (E5). The SQL adapter implements them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from medicalbooking.modules.clinical.domain.evaluation import (
    CriterionSpec,
    Evaluation,
    ResultInput,
    RuleSpec,
)
from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.laboratory.application.ports import AuditEvent


@dataclass(frozen=True)
class ShadowEvidenceRecord:
    rol: str
    resultado_id: str
    valor: Decimal
    unidad: str


@dataclass(frozen=True)
class ShadowItemRecord:
    hallazgo: str
    categoria: str
    corte_valor: Decimal | None
    corte_unidad: str | None
    fuente: str
    motivo: str | None
    evidencia: tuple[ShadowEvidenceRecord, ...]


@dataclass(frozen=True)
class ShadowSnapshotRecord:
    id: str
    orden_id: str
    version_regla: str
    creado_en: datetime
    items: tuple[ShadowItemRecord, ...]


@dataclass(frozen=True)
class AnalyteView:
    id: str
    nombre: str
    unidad: str | None
    tipo_valor: str


@dataclass(frozen=True)
class NewReference:
    id: str
    analito_id: str
    fuente_id: str
    unidad: str
    minimo: Decimal | None
    maximo: Decimal | None
    sexo: str | None
    edad_min_dias: int | None
    edad_max_dias: int | None
    metodo: str | None
    tipo_muestra: str | None
    contexto: str | None
    fecha_inicio: date
    fecha_fin: date | None


@dataclass(frozen=True)
class NewRule:
    id: str
    codigo: str
    nombre: str
    descripcion: str | None
    tipo: str
    expresion_logica: str
    mensaje: str
    prioridad: str
    criteria: tuple[CriterionSpec, ...]


@dataclass(frozen=True)
class OrderForEvaluation:
    order_id: str
    estado: str
    paciente_id: str
    paciente_user_id: str
    medico_user_id: str
    results: tuple[ResultInput, ...]


@dataclass(frozen=True)
class FindingView:
    id: str
    rule_code: str
    rule_version: int
    tipo: str
    explicacion: str
    estado_revision: str
    evidence: tuple[tuple[str, Decimal, str | None], ...]


@dataclass(frozen=True)
class ProposalView:
    id: str
    order_id: str
    estado: str
    version_motor: str
    resumen: str
    creado_en: datetime
    findings: tuple[FindingView, ...]
    decision: str | None
    conclusion_medica: str | None


class ClinicalStore(Protocol):
    # ----- knowledge administration -----

    def list_analytes(self) -> list[AnalyteView]: ...

    def analyte_exists(self, analito_id: str) -> bool: ...

    def source_exists(self, fuente_id: str) -> bool: ...

    def create_analyte(
        self, analito_id: str, nombre: str, unidad: str | None, tipo_valor: str, now: datetime
    ) -> None: ...

    def list_validated_references(self, analito_id: str, on: date) -> list[Reference]: ...

    def create_reference(self, command: NewReference, now: datetime) -> None: ...

    def validate_reference(self, reference_id: str, actor_user_id: str, now: datetime) -> str:
        """Return NOT_FOUND, NOT_DRAFT, OVERLAP or CREATED."""
        ...

    def create_rule(self, command: NewRule, now: datetime) -> int:
        """Store a draft rule with the next version for its code; return that version."""
        ...

    def validate_rule(self, rule_id: str, actor_user_id: str, now: datetime) -> str:
        """Return NOT_FOUND, NOT_DRAFT, UNKNOWN_ANALYTE or CREATED."""
        ...

    # ----- proposals -----

    def load_order_for_evaluation(self, order_id: str) -> OrderForEvaluation | None: ...

    def loinc_results_for_order(self, order_id: str) -> list[tuple[str, str | None, Decimal]]: ...

    def loinc_evidence_for_order(self, order_id: str) -> list[tuple[str, str, str | None, Decimal]]:
        """(result id, loinc, unit, value) of the numeric LOINC results of one order (S9)."""
        ...

    def patient_clinical_profile(
        self, paciente_id: str
    ) -> tuple[int | None, bool | None, int | None]:
        """(altitud_residencia_m, fumador, cigarrillos_dia) of a patient; None means unknown."""
        ...

    def save_patient_clinical_profile(
        self,
        usuario_id: str,
        altitud_residencia_m: int | None,
        fumador: bool | None,
        cigarrillos_dia: int | None,
    ) -> str | None:
        """Store the patient's own profile; return the patient id, or None without a patient."""
        ...

    def record_shadow_snapshot(
        self,
        order_id: str,
        version_regla: str,
        items: tuple[ShadowItemRecord, ...],
        actor_user_id: str,
        now: datetime,
    ) -> str:
        """Insert one immutable snapshot with its items and evidence; return its id."""
        ...

    def patient_sex_birth(self, paciente_id: str) -> tuple[str | None, date | None]: ...

    def validated_rules(self, now: datetime) -> list[RuleSpec]: ...

    def record_proposal(
        self, order_id: str, evaluation: Evaluation, version_motor: str, now: datetime
    ) -> tuple[str, str]:
        """Return (status, proposal_id). Status is CREATED, ALREADY_EXISTS or NOT_COMPLETED."""
        ...

    def get_proposal(self, order_id: str) -> ProposalView | None: ...

    def proposal_owner(self, proposal_id: str) -> OrderForEvaluation | None: ...

    def review_proposal(
        self,
        proposal_id: str,
        medico_user_id: str,
        decision: str,
        motivo: str | None,
        conclusion_corregida: str | None,
        now: datetime,
    ) -> str:
        """Return CREATED, NOT_PENDING (already decided or not reviewable) or NOT_FOUND."""
        ...

    def record_audit(self, event: AuditEvent) -> None: ...

"""Clinical knowledge administration (E5): analytes, reference ranges and declarative rules.

Drafts are created by administrators; only a doctor validates them. Validated rows are
immutable: a change is a new draft. Reads expose validated knowledge only.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from medicalbooking.modules.clinical.application.ports import (
    AnalyteView,
    ClinicalStore,
    NewReference,
    NewRule,
)
from medicalbooking.modules.clinical.domain.evaluation import CriterionSpec
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

PERM_READ = "conocimiento.leer"
PERM_ADMIN = "conocimiento.administrar"
PERM_VALIDATE = "conocimiento.validar"

TIPOS_VALOR = frozenset({"NUMERICO", "TEXTO", "CODIFICADO"})
TIPOS_REGLA = frozenset({"HALLAZGO", "HIPOTESIS", "ALERTA"})
LOGICAS = frozenset({"TODOS", "CUALQUIERA"})
PRIORIDADES = frozenset({"INFORMATIVA", "REVISION_MEDICA", "URGENTE"})
NUMERIC_OPERATORS = frozenset({"LT", "LE", "GT", "GE", "EQ"})
STATUS_OPERATORS = frozenset({"FUERA_REFERENCIA", "DENTRO_REFERENCIA"})
MAX_CRITERIA = 10
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,79}$")


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


class KnowledgeService:
    def __init__(self, store: ClinicalStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    # ---------- reads ----------

    def list_analytes(self, actor: AccessProfile) -> list[AnalyteView]:
        _require(actor, PERM_READ)
        return self._store.list_analytes()

    def list_validated_references(self, actor: AccessProfile, analito_id: str, on: date):
        _require(actor, PERM_READ)
        if not self._store.analyte_exists(analito_id):
            raise NotFound("Analyte not found")
        return self._store.list_validated_references(analito_id, on)

    # ---------- drafts ----------

    def create_analyte(
        self,
        actor: AccessProfile,
        *,
        nombre: str,
        unidad: str | None,
        tipo_valor: str,
    ) -> str:
        _require(actor, PERM_ADMIN)
        if not nombre or len(nombre) > 150:
            raise ValidationFailed("Analyte name is required and must be at most 150 characters")
        if unidad is not None and len(unidad) > 40:
            raise ValidationFailed("Unit is too long")
        if tipo_valor not in TIPOS_VALOR:
            raise ValidationFailed("Unknown value type")
        analito_id = str(uuid.uuid4())
        self._store.create_analyte(analito_id, nombre, unidad, tipo_valor, self._clock())
        return analito_id

    def create_reference(
        self,
        actor: AccessProfile,
        analito_id: str,
        *,
        fuente_id: str,
        unidad: str,
        minimo: Decimal | None,
        maximo: Decimal | None,
        sexo: str | None,
        edad_min_dias: int | None,
        edad_max_dias: int | None,
        metodo: str | None,
        tipo_muestra: str | None,
        contexto: str | None,
        fecha_inicio: date,
        fecha_fin: date | None,
    ) -> str:
        _require(actor, PERM_ADMIN)
        if not unidad or len(unidad) > 40:
            raise ValidationFailed("Unit is required and must be at most 40 characters")
        if minimo is not None and maximo is not None and minimo > maximo:
            raise ValidationFailed("Minimum cannot be greater than maximum")
        if (
            edad_min_dias is not None
            and edad_max_dias is not None
            and edad_min_dias > edad_max_dias
        ):
            raise ValidationFailed("Minimum age cannot be greater than maximum age")
        if fecha_fin is not None and fecha_fin < fecha_inicio:
            raise ValidationFailed("End date cannot be before the start date")
        if not self._store.analyte_exists(analito_id):
            raise NotFound("Analyte not found")
        if not self._store.source_exists(fuente_id):
            raise ValidationFailed("Unknown source")
        reference_id = str(uuid.uuid4())
        self._store.create_reference(
            NewReference(
                id=reference_id,
                analito_id=analito_id,
                fuente_id=fuente_id,
                unidad=unidad,
                minimo=minimo,
                maximo=maximo,
                sexo=sexo,
                edad_min_dias=edad_min_dias,
                edad_max_dias=edad_max_dias,
                metodo=metodo,
                tipo_muestra=tipo_muestra,
                contexto=contexto,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
            ),
            self._clock(),
        )
        return reference_id

    def create_rule(
        self,
        actor: AccessProfile,
        *,
        codigo: str,
        nombre: str,
        descripcion: str | None,
        tipo: str,
        expresion_logica: str,
        mensaje: str,
        prioridad: str,
        criteria: list[CriterionSpec],
    ) -> str:
        _require(actor, PERM_ADMIN)
        if not _CODE.match(codigo):
            raise ValidationFailed("Rule code must be upper case letters, digits, '-' or '_'")
        if not nombre or len(nombre) > 200 or not mensaje or len(mensaje) > 2000:
            raise ValidationFailed("Rule name and message are required and must be short")
        if tipo not in TIPOS_REGLA:
            raise ValidationFailed("Unknown rule type")
        if expresion_logica not in LOGICAS:
            raise ValidationFailed("Logic must be TODOS or CUALQUIERA")
        if prioridad not in PRIORIDADES:
            raise ValidationFailed("Unknown priority")
        if not criteria or len(criteria) > MAX_CRITERIA:
            raise ValidationFailed(f"A rule needs between 1 and {MAX_CRITERIA} criteria")
        for criterion in criteria:
            _check_criterion(criterion)
            if not self._store.analyte_exists(criterion.analito_id):
                raise ValidationFailed("A criterion refers to an unknown analyte")
        rule_id = str(uuid.uuid4())
        self._store.create_rule(
            NewRule(
                id=rule_id,
                codigo=codigo,
                nombre=nombre,
                descripcion=descripcion,
                tipo=tipo,
                expresion_logica=expresion_logica,
                mensaje=mensaje,
                prioridad=prioridad,
                criteria=tuple(criteria),
            ),
            self._clock(),
        )
        return rule_id

    # ---------- validation (clinical sign-off) ----------

    def validate_reference(self, actor: AccessProfile, reference_id: str) -> None:
        _require(actor, PERM_VALIDATE)
        status = self._store.validate_reference(reference_id, actor.user_id, self._clock())
        _raise_for(status, "reference")

    def validate_rule(self, actor: AccessProfile, rule_id: str) -> None:
        _require(actor, PERM_VALIDATE)
        status = self._store.validate_rule(rule_id, actor.user_id, self._clock())
        _raise_for(status, "rule")


def _check_criterion(criterion: CriterionSpec) -> None:
    if criterion.operador in NUMERIC_OPERATORS:
        if criterion.umbral is None:
            raise ValidationFailed("A numeric criterion needs a threshold")
        if not criterion.unidad:
            raise ValidationFailed("A numeric criterion needs the unit of its threshold")
    elif criterion.operador in STATUS_OPERATORS:
        if criterion.umbral is not None:
            raise ValidationFailed("A status criterion takes no threshold")
    else:
        raise ValidationFailed("Unknown criterion operator")


def _raise_for(status: str, what: str) -> None:
    if status == "NOT_FOUND":
        raise NotFound(f"{what.capitalize()} not found")
    if status == "NOT_DRAFT":
        raise Conflict(f"Only a draft {what} can be validated")
    if status == "OVERLAP":
        raise Conflict("The reference overlaps a validated reference for the same analyte")
    if status == "UNKNOWN_ANALYTE":
        raise ValidationFailed("The rule refers to an analyte that does not exist")

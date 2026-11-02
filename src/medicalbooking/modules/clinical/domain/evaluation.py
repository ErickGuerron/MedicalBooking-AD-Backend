"""Deterministic rule evaluation (E5). Pure functions: the same input gives the same proposal.

A rule fires only on inputs that are available. Findings are preliminary: they never state
a diagnosis, and every finding keeps the rule version and the evidence it was based on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from operator import eq, ge, gt, le, lt

from medicalbooking.modules.clinical.domain.references import ALTO, BAJO, DENTRO

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_SIN_EVIDENCIA = "SIN_EVIDENCIA"
MOTOR_VERSION = "e5-deterministic-1"

_NUMERIC: dict[str, Callable[[Decimal, Decimal], bool]] = {
    "LT": lt,
    "LE": le,
    "GT": gt,
    "GE": ge,
    "EQ": eq,
}
_CLASSIFIED = (BAJO, DENTRO, ALTO)


@dataclass(frozen=True)
class ResultInput:
    resultado_id: str
    analito_id: str
    valor: Decimal | None
    unidad: str | None
    interpretacion: str | None


@dataclass(frozen=True)
class CriterionSpec:
    analito_id: str
    operador: str
    umbral: Decimal | None
    unidad: str | None


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    codigo: str
    version: int
    tipo: str
    expresion_logica: str
    mensaje: str
    criteria: tuple[CriterionSpec, ...]


@dataclass(frozen=True)
class Evidence:
    resultado_id: str
    valor: Decimal
    unidad: str | None


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_code: str
    rule_version: int
    tipo: str
    explicacion: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Evaluation:
    estado: str
    findings: tuple[Finding, ...]
    resumen: str


def _single_result(results: list[ResultInput], analito_id: str) -> ResultInput | None:
    """An analyte with two results is ambiguous for a rule: it is not used."""
    matching = [r for r in results if r.analito_id == analito_id]
    return matching[0] if len(matching) == 1 else None


def _criterion(
    criterion: CriterionSpec, results: list[ResultInput]
) -> tuple[bool | None, ResultInput | None]:
    """Return (satisfied, used_result). satisfied is None when the input is unavailable."""
    result = _single_result(results, criterion.analito_id)
    if result is None:
        return None, None
    if criterion.operador in _NUMERIC:
        if result.valor is None or criterion.umbral is None:
            return None, None
        # A numeric threshold only means something in its own unit: no unit, no comparison.
        if criterion.unidad is None or result.unidad != criterion.unidad:
            return None, None
        return _NUMERIC[criterion.operador](result.valor, criterion.umbral), result
    if criterion.operador == "FUERA_REFERENCIA":
        if result.interpretacion in (BAJO, ALTO):
            return True, result
        if result.interpretacion == DENTRO:
            return False, result
        return None, None
    if criterion.operador == "DENTRO_REFERENCIA":
        if result.interpretacion == DENTRO:
            return True, result
        if result.interpretacion in (BAJO, ALTO):
            return False, result
        return None, None
    return None, None


def _evaluate_rule(rule: RuleSpec, results: list[ResultInput]) -> Finding | None:
    outcomes = [_criterion(criterion, results) for criterion in rule.criteria]
    if rule.expresion_logica == "TODOS":
        # Every input must be available and satisfied; any gap means the rule does not run.
        fires = all(satisfied is True for satisfied, _ in outcomes)
    else:  # CUALQUIERA: at least one satisfied criterion; missing inputs do not count.
        fires = any(satisfied is True for satisfied, _ in outcomes)
    if not fires:
        return None
    # One evidence row per result: two criteria on one analyte must not duplicate it (the
    # evidence key is (finding, result)).
    by_result = {
        used.resultado_id: Evidence(
            resultado_id=used.resultado_id, valor=used.valor, unidad=used.unidad
        )
        for satisfied, used in outcomes
        if satisfied is True and used is not None
    }
    evidence = [by_result[key] for key in sorted(by_result)]
    return Finding(
        rule_id=rule.rule_id,
        rule_code=rule.codigo,
        rule_version=rule.version,
        tipo=rule.tipo,
        explicacion=rule.mensaje,
        evidence=tuple(evidence),
    )


def evaluate(results: list[ResultInput], rules: list[RuleSpec]) -> Evaluation:
    findings = [
        finding
        for rule in sorted(rules, key=lambda item: (item.codigo, item.version))
        if (finding := _evaluate_rule(rule, results)) is not None
    ]
    classified = sum(1 for r in results if r.interpretacion in _CLASSIFIED)
    estado = ESTADO_PENDIENTE if classified or findings else ESTADO_SIN_EVIDENCIA
    resumen = (
        f"{classified} resultado(s) clasificado(s); {len(findings)} hallazgo(s) preliminar(es). "
        "Propuesta preliminar: no es un diagnostico."
    )
    return Evaluation(estado=estado, findings=tuple(findings), resumen=resumen)


__all__ = [
    "CriterionSpec",
    "ESTADO_PENDIENTE",
    "ESTADO_SIN_EVIDENCIA",
    "Evaluation",
    "Evidence",
    "Finding",
    "MOTOR_VERSION",
    "ResultInput",
    "RuleSpec",
    "evaluate",
]

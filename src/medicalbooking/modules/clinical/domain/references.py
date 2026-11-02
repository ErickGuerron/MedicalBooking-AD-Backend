"""Reference ranges (E5): applicability, classification and overlap. Pure functions.

Three rules keep a clinical category honest:
- A reference applies only when every constraint it sets is known to match the patient.
- When a constraint is unknown the result is NO_EVALUABLE: no category is invented.
- Exactly one applicable reference is required; zero or several is NO_EVALUABLE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

BAJO = "BAJO"
DENTRO = "DENTRO_REFERENCIA"
ALTO = "ALTO"
NO_EVALUABLE = "NO_EVALUABLE"

_YES, _NO, _UNKNOWN = "yes", "no", "unknown"


@dataclass(frozen=True)
class Reference:
    id: str
    version: int
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
class PatientContext:
    """What is known about the patient and the sample. None means unknown."""

    sexo: str | None
    edad_dias: int | None
    metodo: str | None = None
    tipo_muestra: str | None = None
    contexto: str | None = None


@dataclass(frozen=True)
class Classification:
    interpretacion: str
    reference: Reference | None
    motivo: str | None = None


def _vigente(reference: Reference, on: date) -> bool:
    return reference.fecha_inicio <= on and (
        reference.fecha_fin is None or on <= reference.fecha_fin
    )


def _match_exact(required: str | None, known: str | None) -> str:
    if required is None:
        return _YES
    if known is None:
        return _UNKNOWN
    return _YES if required == known else _NO


def _match_age(reference: Reference, age_days: int | None) -> str:
    if reference.edad_min_dias is None and reference.edad_max_dias is None:
        return _YES
    if age_days is None:
        return _UNKNOWN
    if reference.edad_min_dias is not None and age_days < reference.edad_min_dias:
        return _NO
    if reference.edad_max_dias is not None and age_days > reference.edad_max_dias:
        return _NO
    return _YES


def _applies(reference: Reference, patient: PatientContext) -> str:
    checks = [
        _match_exact(reference.sexo, patient.sexo),
        _match_age(reference, patient.edad_dias),
        _match_exact(reference.metodo, patient.metodo),
        _match_exact(reference.tipo_muestra, patient.tipo_muestra),
        _match_exact(reference.contexto, patient.contexto),
    ]
    if _NO in checks:
        return _NO
    if _UNKNOWN in checks:
        return _UNKNOWN
    return _YES


def _interpret(value: Decimal, reference: Reference) -> str:
    if reference.minimo is not None and value < reference.minimo:
        return BAJO
    if reference.maximo is not None and value > reference.maximo:
        return ALTO
    return DENTRO


def classify(
    value: Decimal | None,
    unidad: str | None,
    references: list[Reference],
    patient: PatientContext,
    on: date,
) -> Classification:
    if value is None:
        return Classification(NO_EVALUABLE, None, "no numeric value")
    if unidad is None:
        return Classification(NO_EVALUABLE, None, "unit missing")
    candidates = [r for r in references if r.unidad == unidad and _vigente(r, on)]
    if not candidates:
        return Classification(NO_EVALUABLE, None, "no reference for this unit and date")

    statuses = [(reference, _applies(reference, patient)) for reference in candidates]
    if any(status == _UNKNOWN for _, status in statuses):
        return Classification(NO_EVALUABLE, None, "patient or sample context is missing")
    applicable = [reference for reference, status in statuses if status == _YES]
    if not applicable:
        return Classification(NO_EVALUABLE, None, "no reference applies to this patient")
    if len(applicable) > 1:
        return Classification(NO_EVALUABLE, None, "more than one reference applies")
    reference = applicable[0]
    return Classification(_interpret(value, reference), reference)


def _wild(left, right) -> bool:
    """None is a wildcard: it overlaps every value of the other side."""
    return left is None or right is None or left == right


def _intervals_meet(a_min, a_max, b_min, b_max) -> bool:
    a_starts_before_b_ends = a_min is None or b_max is None or a_min <= b_max
    b_starts_before_a_ends = b_min is None or a_max is None or b_min <= a_max
    return a_starts_before_b_ends and b_starts_before_a_ends


def _validity_meets(a: Reference, b: Reference) -> bool:
    a_ends_after_b_starts = a.fecha_fin is None or a.fecha_fin >= b.fecha_inicio
    b_ends_after_a_starts = b.fecha_fin is None or b.fecha_fin >= a.fecha_inicio
    return a_ends_after_b_starts and b_ends_after_a_starts


def overlaps(a: Reference, b: Reference) -> bool:
    """True when some patient could match both references in the same moment."""
    return (
        a.unidad == b.unidad
        and _wild(a.sexo, b.sexo)
        and _wild(a.metodo, b.metodo)
        and _wild(a.tipo_muestra, b.tipo_muestra)
        and _wild(a.contexto, b.contexto)
        and _intervals_meet(a.edad_min_dias, a.edad_max_dias, b.edad_min_dias, b.edad_max_dias)
        and _validity_meets(a, b)
    )

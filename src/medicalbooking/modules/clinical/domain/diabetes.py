"""Diabetes finding (clinical findings S4, S3): ADA 2026 Table 2.1, 2.2 and recommendation 2.4.
Pure functions.

- One abnormal result never gives a diagnosis: PENDIENTE_CONFIRMACION.
- Confirmation: two abnormal results from different tests (same moment allowed), or the same
  test at two time points.
- Random glucose >= 200 mg/dL counts alone only with classic symptoms.
- With anaemia, HbA1c is not used (recommendation 2.4).
Units: glucose mg/dL, HbA1c %. A suggestion for physician review, never a diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DIABETES_CONFIRMADA = "DIABETES_CONFIRMADA"
PENDIENTE_CONFIRMACION = "PENDIENTE_CONFIRMACION"
PREDIABETES = "PREDIABETES"
SIN_EVIDENCIA_DIABETES = "SIN_EVIDENCIA_DIABETES"
NO_EVALUABLE = "NO_EVALUABLE"

_FPG_DIABETES = Decimal("126")
_FPG_PREDIABETES = Decimal("100")
_A1C_DIABETES = Decimal("6.5")
_A1C_PREDIABETES = Decimal("5.7")
_OGTT_DIABETES = Decimal("200")
_OGTT_PREDIABETES = Decimal("140")
_RANDOM_DIABETES = Decimal("200")

_NOTE_ANEMIA = "HbA1c no valida con anemia: se usan glucosa en ayunas o PTOG (ADA 2026, 2.4)"


@dataclass(frozen=True)
class DiabetesFinding:
    categoria: str
    motivo: str | None = None


def _count_hits(values: list[Decimal], threshold: Decimal) -> int:
    return sum(1 for value in values if value >= threshold)


def diabetes_finding(
    fasting_mg_dl: list[Decimal],
    hba1c_pct: list[Decimal],
    ogtt_2h_mg_dl: list[Decimal],
    random_mg_dl: Decimal | None,
    symptoms: bool | None,
    *,
    anemia: bool,
) -> DiabetesFinding:
    """Classify diabetes from the exams in the system, with ADA confirmation rules."""
    use_hba1c = not anemia
    note = _NOTE_ANEMIA if anemia and hba1c_pct else None
    a1c = hba1c_pct if use_hba1c else []

    if random_mg_dl is not None and symptoms is True and random_mg_dl >= _RANDOM_DIABETES:
        return DiabetesFinding(DIABETES_CONFIRMADA, "glucosa aleatoria >= 200 con sintomas")

    hits = {
        "fpg": _count_hits(fasting_mg_dl, _FPG_DIABETES),
        "a1c": _count_hits(a1c, _A1C_DIABETES),
        "ogtt": _count_hits(ogtt_2h_mg_dl, _OGTT_DIABETES),
    }
    tests_with_hits = [count for count in hits.values() if count > 0]
    if any(count >= 2 for count in tests_with_hits) or len(tests_with_hits) >= 2:
        return DiabetesFinding(DIABETES_CONFIRMADA, note)
    if tests_with_hits:
        return DiabetesFinding(
            PENDIENTE_CONFIRMACION, note or "un resultado anormal: falta confirmacion"
        )

    prediabetes = (
        _count_hits_in_band(fasting_mg_dl, _FPG_PREDIABETES, _FPG_DIABETES)
        + _count_hits_in_band(a1c, _A1C_PREDIABETES, _A1C_DIABETES)
        + _count_hits_in_band(ogtt_2h_mg_dl, _OGTT_PREDIABETES, _OGTT_DIABETES)
    )
    usable = bool(fasting_mg_dl or ogtt_2h_mg_dl or a1c)
    if prediabetes:
        return DiabetesFinding(PREDIABETES, note)
    if usable:
        return DiabetesFinding(SIN_EVIDENCIA_DIABETES, note)
    if anemia and hba1c_pct:
        return DiabetesFinding(NO_EVALUABLE, _NOTE_ANEMIA)
    if random_mg_dl is not None and symptoms is not True:
        return DiabetesFinding(
            NO_EVALUABLE, "glucosa aleatoria sin sintomas no es criterio diagnostico"
        )
    return DiabetesFinding(NO_EVALUABLE, "sin glucosa en ayunas, PTOG ni HbA1c utilizable")


def _count_hits_in_band(values: list[Decimal], low: Decimal, high: Decimal) -> int:
    return sum(1 for value in values if low <= value < high)

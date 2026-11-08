"""Diabetes finding (clinical findings S4, S3): ADA 2026 Tables 2.1, 2.2 and recommendation 2.4.

Rules:
- One abnormal result never gives a diagnosis: it is PENDIENTE_CONFIRMACION.
- Confirmation needs two abnormal results from different tests (possibly the same moment),
  or the same test at two time points (ADA 2026 Table 2.1 footnote).
- Random glucose >= 200 mg/dL counts alone only with classic symptoms (Table 2.1).
- When the patient has anaemia, HbA1c is not used (ADA recommendation 2.4).
Units: glucose mg/dL, HbA1c %. This is a suggestion for physician review, never a diagnosis.
"""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.diabetes import (
    DIABETES_CONFIRMADA,
    NO_EVALUABLE,
    PENDIENTE_CONFIRMACION,
    PREDIABETES,
    SIN_EVIDENCIA_DIABETES,
    diabetes_finding,
)


def d(value: str) -> Decimal:
    return Decimal(value)


def ds(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


def test_two_fasting_readings_at_two_times_confirm() -> None:
    finding = diabetes_finding(ds("130", "128"), [], [], None, None, anemia=False)
    assert finding.categoria == DIABETES_CONFIRMADA


def test_fasting_and_hba1c_in_the_same_draw_confirm() -> None:
    finding = diabetes_finding(ds("126"), ds("6.5"), [], None, None, anemia=False)
    assert finding.categoria == DIABETES_CONFIRMADA


def test_fasting_and_ogtt_confirm() -> None:
    finding = diabetes_finding(ds("130"), [], ds("200"), None, None, anemia=False)
    assert finding.categoria == DIABETES_CONFIRMADA


def test_single_diabetic_fasting_result_is_pending_confirmation() -> None:
    finding = diabetes_finding(ds("130"), [], [], None, None, anemia=False)
    assert finding.categoria == PENDIENTE_CONFIRMACION


def test_random_glucose_with_symptoms_confirms_alone() -> None:
    finding = diabetes_finding([], [], [], d("210"), True, anemia=False)
    assert finding.categoria == DIABETES_CONFIRMADA


def test_random_glucose_without_symptoms_does_not_count() -> None:
    finding = diabetes_finding([], [], [], d("210"), False, anemia=False)
    assert finding.categoria == NO_EVALUABLE
    assert "aleatoria" in finding.motivo


def test_fasting_only_prediabetes_range_is_prediabetes() -> None:
    finding = diabetes_finding(ds("110"), [], [], None, None, anemia=False)
    assert finding.categoria == PREDIABETES


def test_one_diabetic_fasting_and_prediabetic_a1c_is_pending() -> None:
    finding = diabetes_finding(ds("130"), ds("5.9"), [], None, None, anemia=False)
    assert finding.categoria == PENDIENTE_CONFIRMACION


def test_normal_results_are_no_evidence() -> None:
    finding = diabetes_finding(ds("88"), ds("5.2"), ds("120"), None, None, anemia=False)
    assert finding.categoria == SIN_EVIDENCIA_DIABETES


def test_no_readings_is_not_evaluable() -> None:
    finding = diabetes_finding([], [], [], None, None, anemia=False)
    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo == "sin glucosa en ayunas, PTOG ni HbA1c utilizable"


def test_anaemia_ignores_hba1c_for_confirmation() -> None:
    finding = diabetes_finding([], ds("7.0", "7.2"), [], None, None, anemia=True)
    assert finding.categoria == NO_EVALUABLE
    assert "HbA1c no valida con anemia" in finding.motivo


def test_anaemia_uses_fasting_glucose_instead_of_hba1c() -> None:
    finding = diabetes_finding(ds("130"), ds("7.0"), [], None, None, anemia=True)
    assert finding.categoria == PENDIENTE_CONFIRMACION
    assert "HbA1c no valida con anemia" in finding.motivo


def test_anaemia_does_not_confirm_fasting_plus_hba1c() -> None:
    finding = diabetes_finding(ds("130"), ds("6.5"), [], None, None, anemia=True)
    assert finding.categoria == PENDIENTE_CONFIRMACION

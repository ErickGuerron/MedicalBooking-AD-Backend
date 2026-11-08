"""Iron deficiency finding (clinical findings S5, S3), expanded to WHO 2020 Table 1 (approved).

Source: WHO 2020 guideline on use of ferritin concentrations to assess iron status (ISBN
978-92-4-000012-4), Table 1: iron deficiency cut-offs in ug/L by age group, for apparently healthy
individuals and for individuals with infection or inflammation (CRP > 5 mg/L or AGP > 1 g/L).
Pregnancy: only the first trimester has a cut-off (< 15 ug/L); other trimesters are not evaluable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.iron import (
    DEFICIENCIA_HIERRO,
    DEFICIENCIA_HIERRO_CON_INFLAMACION,
    NO_EVALUABLE,
    SIN_DEFICIENCIA,
    SIN_DEFICIENCIA_CON_INFLAMACION,
    iron_finding,
)

NOT_PREGNANT = "NO"


def d(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize(
    ("age_months", "cutoff"),
    [(0, 12), (5, 12), (6, 12), (23, 12), (24, 12), (59, 12), (60, 15), (143, 15)],
)
def test_young_children_groups_use_table_1_cutoffs(age_months, cutoff) -> None:
    below = iron_finding(d(str(cutoff - Decimal("0.1"))), d("1.0"), "M", age_months, None)
    at = iron_finding(d(str(cutoff)), d("1.0"), "M", age_months, None)
    assert below.categoria == DEFICIENCIA_HIERRO
    assert at.categoria == SIN_DEFICIENCIA
    assert below.corte_ug_l == cutoff


@pytest.mark.parametrize(
    ("age_months", "sex", "pregnancy"),
    [
        (5 * 12, "M", None),  # 5 years
        (10 * 12, "M", None),  # 10 years
        (15 * 12, "M", None),  # adolescent boy
        (19 * 12 + 11, "F", NOT_PREGNANT),  # last adolescent month
        (20 * 12, "M", None),  # adult man
        (59 * 12 + 11, "M", None),  # last adult month
        (60 * 12, "M", None),  # 60 years: older persons
        (90 * 12, "F", NOT_PREGNANT),  # older woman
    ],
)
def test_older_groups_use_15_for_both_sexes(age_months, sex, pregnancy) -> None:
    finding = iron_finding(d("14.9"), d("1.0"), sex, age_months, pregnancy)
    assert finding.categoria == DEFICIENCIA_HIERRO
    assert finding.corte_ug_l == 15


def test_older_adult_at_15_is_sin_deficiencia() -> None:
    finding = iron_finding(d("15.0"), d("1.0"), "M", 40 * 12, None)
    assert finding.categoria == SIN_DEFICIENCIA


def test_pregnant_first_trimester_uses_15() -> None:
    assert iron_finding(d("14.9"), d("1.0"), "F", 28 * 12, "T1").categoria == DEFICIENCIA_HIERRO
    assert iron_finding(d("15.0"), d("1.0"), "F", 28 * 12, "T1").categoria == SIN_DEFICIENCIA


def test_pregnant_second_and_third_trimester_are_not_evaluable() -> None:
    for trimester in ("T2", "T3"):
        finding = iron_finding(d("10.0"), d("1.0"), "F", 28 * 12, trimester)
        assert finding.categoria == NO_EVALUABLE
        assert "primer trimestre" in finding.motivo


def test_woman_with_unknown_pregnancy_is_not_evaluable() -> None:
    finding = iron_finding(d("10.0"), d("1.0"), "F", 30 * 12, None)
    assert finding.categoria == NO_EVALUABLE
    assert "embarazo" in finding.motivo


def test_pregnancy_on_a_man_is_not_applicable() -> None:
    finding = iron_finding(d("10.0"), d("1.0"), "M", 40 * 12, "T1")
    assert finding.categoria == NO_EVALUABLE
    assert "embarazo" in finding.motivo


def test_girl_of_10_must_declare_pregnancy() -> None:
    finding = iron_finding(d("10.0"), d("1.0"), "F", 10 * 12, None)
    assert finding.categoria == NO_EVALUABLE


def test_crp_at_5_mg_l_is_still_healthy_threshold() -> None:
    finding = iron_finding(d("14.0"), d("5.0"), "F", 30 * 12, NOT_PREGNANT)
    assert finding.categoria == DEFICIENCIA_HIERRO


def test_inflammation_uses_higher_cutoff_for_children() -> None:
    below = iron_finding(d("25.0"), d("5.1"), "M", 24, None)
    assert below.categoria == DEFICIENCIA_HIERRO_CON_INFLAMACION
    assert below.corte_ug_l == 30


def test_inflammation_uses_70_for_adults() -> None:
    below = iron_finding(d("60.0"), d("8.0"), "F", 30 * 12, NOT_PREGNANT)
    assert below.categoria == DEFICIENCIA_HIERRO_CON_INFLAMACION
    assert below.corte_ug_l == 70
    above = iron_finding(d("70.0"), d("8.0"), "F", 30 * 12, NOT_PREGNANT)
    assert above.categoria == SIN_DEFICIENCIA_CON_INFLAMACION


def test_inflammation_in_first_trimester_is_not_evaluable() -> None:
    finding = iron_finding(d("10.0"), d("8.0"), "F", 28 * 12, "T1")
    assert finding.categoria == NO_EVALUABLE
    assert "inflamacion" in finding.motivo


def test_missing_ferritin_is_not_evaluable() -> None:
    finding = iron_finding(None, d("1.0"), "F", 30 * 12, NOT_PREGNANT)
    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo == "sin ferritina"


def test_missing_crp_is_not_evaluable() -> None:
    finding = iron_finding(d("10.0"), None, "F", 30 * 12, NOT_PREGNANT)
    assert finding.categoria == NO_EVALUABLE
    assert "PCR" in finding.motivo


def test_missing_age_is_not_evaluable() -> None:
    finding = iron_finding(d("10.0"), d("1.0"), "F", None, NOT_PREGNANT)
    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo == "sin edad"

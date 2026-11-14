"""Anaemia finding (clinical findings S2, S3, S6): WHO 2024 Tables 2 and 3. Pure domain.

Hemoglobin is given in g/dL (as in the lab exam) and compared in g/L (WHO 2024).
A missing input is never a category: it is NO_EVALUABLE with a reason.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.anemia import (
    ANEMIA_GRAVE,
    ANEMIA_LEVE,
    ANEMIA_MODERADA,
    NO_EVALUABLE,
    SIN_ANEMIA,
    anemia_finding,
)

NOT_PREGNANT = "NO"


def hb(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize(
    ("value_g_dl", "expected"),
    [
        ("10.5", SIN_ANEMIA),  # 105 g/L is the lower bound of non-anaemia (inclusive)
        ("10.4", ANEMIA_LEVE),  # 104 g/L
        ("9.5", ANEMIA_LEVE),  # 95 g/L (mild lower bound)
        ("9.4", ANEMIA_MODERADA),  # 94 g/L
        ("7.0", ANEMIA_MODERADA),  # 70 g/L (moderate lower bound)
        ("6.9", ANEMIA_GRAVE),  # 69 g/L
    ],
)
def test_infant_6_to_23_months_bands(value_g_dl, expected) -> None:
    finding = anemia_finding(hb(value_g_dl), "M", 12, None)
    assert finding.categoria == expected
    assert finding.corte_g_l == 105


@pytest.mark.parametrize(
    ("value_g_dl", "expected"),
    [
        ("12.0", SIN_ANEMIA),  # women not pregnant: 120 g/L
        ("11.9", ANEMIA_LEVE),
        ("11.0", ANEMIA_LEVE),  # 110 g/L
        ("10.9", ANEMIA_MODERADA),
        ("8.0", ANEMIA_MODERADA),  # 80 g/L
        ("7.9", ANEMIA_GRAVE),
    ],
)
def test_non_pregnant_woman_15_to_65_bands(value_g_dl, expected) -> None:
    finding = anemia_finding(hb(value_g_dl), "F", 34 * 12, NOT_PREGNANT)
    assert finding.categoria == expected
    assert finding.corte_g_l == 120


@pytest.mark.parametrize(
    ("value_g_dl", "expected"),
    [
        ("13.0", SIN_ANEMIA),  # men: 130 g/L
        ("12.9", ANEMIA_LEVE),
        ("11.0", ANEMIA_LEVE),
        ("10.9", ANEMIA_MODERADA),
        ("7.9", ANEMIA_GRAVE),
    ],
)
def test_man_15_to_65_bands(value_g_dl, expected) -> None:
    finding = anemia_finding(hb(value_g_dl), "M", 40 * 12, None)
    assert finding.categoria == expected
    assert finding.corte_g_l == 130


@pytest.mark.parametrize(
    ("trimester", "value_g_dl", "expected", "cutoff"),
    [
        ("T1", "11.0", SIN_ANEMIA, 110),
        ("T1", "10.9", ANEMIA_LEVE, 110),
        ("T2", "10.5", SIN_ANEMIA, 105),  # second trimester uses 105 g/L
        ("T2", "10.4", ANEMIA_LEVE, 105),
        ("T2", "9.4", ANEMIA_MODERADA, 105),
        ("T2", "6.9", ANEMIA_GRAVE, 105),
        ("T3", "11.0", SIN_ANEMIA, 110),
        ("T3", "9.9", ANEMIA_MODERADA, 110),
    ],
)
def test_pregnancy_trimester_cutoffs(trimester, value_g_dl, expected, cutoff) -> None:
    finding = anemia_finding(hb(value_g_dl), "F", 28 * 12, trimester)
    assert finding.categoria == expected
    assert finding.corte_g_l == cutoff


def test_child_5_to_11_years_uses_115() -> None:
    finding = anemia_finding(hb("11.4"), "M", 8 * 12, None)
    assert finding.categoria == ANEMIA_LEVE
    assert finding.corte_g_l == 115


def test_child_12_to_14_uses_120_for_both_sexes() -> None:
    assert anemia_finding(hb("12.0"), "F", 13 * 12, None).categoria == SIN_ANEMIA
    assert anemia_finding(hb("12.0"), "M", 13 * 12, None).categoria == SIN_ANEMIA


def test_missing_hemoglobin_is_not_evaluable() -> None:
    finding = anemia_finding(None, "F", 34 * 12, NOT_PREGNANT)
    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo == "sin valor de hemoglobina"


def test_adult_woman_with_unknown_pregnancy_is_not_evaluable() -> None:
    finding = anemia_finding(hb("11.0"), "F", 34 * 12, None)
    assert finding.categoria == NO_EVALUABLE
    assert "embarazo" in finding.motivo


def test_adult_with_unknown_sex_is_not_evaluable() -> None:
    finding = anemia_finding(hb("11.0"), None, 40 * 12, None)
    assert finding.categoria == NO_EVALUABLE
    assert "sexo" in finding.motivo


def test_under_six_months_has_no_cutoff() -> None:
    finding = anemia_finding(hb("9.0"), "M", 5, None)  # 5 months
    assert finding.categoria == NO_EVALUABLE
    assert "6 meses" in finding.motivo


def test_over_65_is_outside_the_who_table() -> None:
    finding = anemia_finding(hb("9.0"), "M", 70 * 12, None)
    assert finding.categoria == NO_EVALUABLE
    assert "65" in finding.motivo


def test_pregnancy_on_a_man_is_not_applicable() -> None:
    finding = anemia_finding(hb("11.0"), "M", 40 * 12, "T1")
    assert finding.categoria == NO_EVALUABLE
    assert "embarazo" in finding.motivo

"""Iron deficiency with the AGA 2020 cut-off for anaemic adults (clinical findings S17).

Source: Ko CW et al., AGA Clinical Practice Guidelines on the Gastrointestinal Evaluation of Iron
Deficiency Anemia, Gastroenterology 2020;159:1085-1094, recommendation 1: in patients with anaemia,
a ferritin cut-off of 45 ng/mL over 15 ng/mL. Applies to adults (18 years or older), not pregnant,
with CRP <= 5 mg/L. Every other case keeps the WHO 2020 Table 1 rule (S5).
"""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.iron import (
    DEFICIENCIA_HIERRO,
    DEFICIENCIA_HIERRO_CON_INFLAMACION,
    SIN_DEFICIENCIA,
    iron_finding,
)

NOT_PREGNANT = "NO"
FUENTE_AGA = "AGA 2020, recomendacion 1"
FUENTE_WHO = "WHO 2020, Tabla 1"
ADULT_30 = 30 * 12
ADULT_18 = 18 * 12
ADULT_17 = 17 * 12 + 11


def d(value: str) -> Decimal:
    return Decimal(value)


def test_anaemic_adult_below_45_is_deficient_with_aga_cutoff() -> None:
    finding = iron_finding(d("44.9"), d("1.0"), "M", ADULT_30, None, anemia_presente=True)
    assert finding.categoria == DEFICIENCIA_HIERRO
    assert finding.corte_ug_l == 45
    assert finding.fuente == FUENTE_AGA


def test_anaemic_adult_at_45_is_not_deficient() -> None:
    finding = iron_finding(d("45.0"), d("1.0"), "M", ADULT_30, None, anemia_presente=True)
    assert finding.categoria == SIN_DEFICIENCIA
    assert finding.corte_ug_l == 45


def test_anaemic_non_pregnant_woman_uses_aga_cutoff() -> None:
    finding = iron_finding(d("44.0"), d("1.0"), "F", ADULT_30, NOT_PREGNANT, anemia_presente=True)
    assert finding.categoria == DEFICIENCIA_HIERRO
    assert finding.corte_ug_l == 45


def test_anaemic_adult_at_18_years_uses_aga_cutoff() -> None:
    finding = iron_finding(d("30.0"), d("1.0"), "M", ADULT_18, None, anemia_presente=True)
    assert finding.categoria == DEFICIENCIA_HIERRO
    assert finding.corte_ug_l == 45


def test_anaemic_adolescent_under_18_keeps_who_cutoff() -> None:
    finding = iron_finding(d("20.0"), d("1.0"), "M", ADULT_17, None, anemia_presente=True)
    assert finding.categoria == SIN_DEFICIENCIA
    assert finding.corte_ug_l == 15
    assert finding.fuente == FUENTE_WHO


def test_anaemic_young_child_keeps_who_cutoff() -> None:
    finding = iron_finding(d("20.0"), d("1.0"), "M", 3 * 12, None, anemia_presente=True)
    assert finding.categoria == SIN_DEFICIENCIA
    assert finding.corte_ug_l == 12
    assert finding.fuente == FUENTE_WHO


def test_without_anaemia_keeps_who_cutoff() -> None:
    finding = iron_finding(d("30.0"), d("1.0"), "M", ADULT_30, None, anemia_presente=False)
    assert finding.categoria == SIN_DEFICIENCIA
    assert finding.corte_ug_l == 15
    assert finding.fuente == FUENTE_WHO


def test_default_keeps_who_cutoff_for_existing_callers() -> None:
    finding = iron_finding(d("30.0"), d("1.0"), "M", ADULT_30, None)
    assert finding.corte_ug_l == 15
    assert finding.fuente == FUENTE_WHO


def test_anaemic_woman_in_first_trimester_keeps_who_pregnancy_cutoff() -> None:
    finding = iron_finding(d("30.0"), d("1.0"), "F", ADULT_30, "T1", anemia_presente=True)
    assert finding.categoria == SIN_DEFICIENCIA
    assert finding.corte_ug_l == 15
    assert finding.fuente == FUENTE_WHO


def test_anaemic_with_inflammation_keeps_who_inflammation_cutoff() -> None:
    finding = iron_finding(d("60.0"), d("6.0"), "M", ADULT_30, None, anemia_presente=True)
    assert finding.categoria == DEFICIENCIA_HIERRO_CON_INFLAMACION
    assert finding.corte_ug_l == 70
    assert finding.fuente == FUENTE_WHO

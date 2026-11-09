"""Anaemia finding with the WHO 2024 altitude and smoking adjustment (feature doc S12, S3).

The adjustment is subtracted from the observed haemoglobin before comparing with the cut-off and the
severity bands. When the adjustment is unknown the shadow report marks anaemia NO_EVALUABLE; it is
never assumed to be zero (see test_shadow_report for that path).
"""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.anemia import (
    ANEMIA_LEVE,
    ANEMIA_MODERADA,
    SIN_ANEMIA,
    anemia_finding,
)
from medicalbooking.modules.clinical.domain.shadow import ShadowInputs, shadow_report

NOT_PREGNANT = "NO"


def hb(value: str) -> Decimal:
    return Decimal(value)


def test_adjustment_makes_a_borderline_value_anaemic() -> None:
    # Women 15-65 cut-off is 120 g/L. 12.0 g/dL is no anaemia; with a 4 g/L adjustment it is 116.
    assert anemia_finding(hb("12.0"), "F", 34 * 12, NOT_PREGNANT).categoria == SIN_ANEMIA

    adjusted = anemia_finding(hb("12.0"), "F", 34 * 12, NOT_PREGNANT, ajuste_g_l=4)

    assert adjusted.categoria == ANEMIA_LEVE
    assert adjusted.corte_g_l == 120


def test_severity_uses_the_adjusted_value() -> None:
    # 11.0 g/dL is 110 g/L: mild for a woman. With a 6 g/L adjustment it is 104 g/L: moderate.
    assert anemia_finding(hb("11.0"), "F", 34 * 12, NOT_PREGNANT).categoria == ANEMIA_LEVE
    assert (
        anemia_finding(hb("11.0"), "F", 34 * 12, NOT_PREGNANT, ajuste_g_l=6).categoria
        == ANEMIA_MODERADA
    )


def test_zero_adjustment_keeps_the_previous_result() -> None:
    assert anemia_finding(hb("11.0"), "F", 34 * 12, NOT_PREGNANT, ajuste_g_l=0).categoria == (
        anemia_finding(hb("11.0"), "F", 34 * 12, NOT_PREGNANT).categoria
    )


def test_unknown_adjustment_in_the_shadow_report_is_not_evaluable_not_zero() -> None:
    report = shadow_report(
        ShadowInputs(
            sex="F",
            age_months=34 * 12,
            pregnancy=NOT_PREGNANT,
            hemoglobin_g_dl=hb("12.0"),
            ferritin_ug_l=None,
            crp_mg_l=None,
            ajuste_g_l=None,
        )
    )

    assert report.anemia.categoria == "NO_EVALUABLE"
    assert "altitud" in report.anemia.motivo


def _shadow_anemia(hemoglobin: str | None, ajuste: int | None, sex: str = "F"):
    return shadow_report(
        ShadowInputs(
            sex=sex,
            age_months=34 * 12,
            pregnancy=NOT_PREGNANT,
            hemoglobin_g_dl=None if hemoglobin is None else hb(hemoglobin),
            ferritin_ug_l=None,
            crp_mg_l=None,
            ajuste_g_l=ajuste,
        )
    ).anemia


def test_an_unknown_adjustment_reason_says_altitude_or_smoking_is_not_usable() -> None:
    # Exactly 20 cigarettes a day, or altitude above 4999 m, leave the adjustment unknown.
    # The reason must say the value is outside the table, not claim the data is missing.
    motivo = _shadow_anemia("12.0", None).motivo

    assert "ajuste" in motivo
    assert "fuera de la tabla" in motivo


def test_a_missing_haemoglobin_is_reported_before_the_unknown_adjustment() -> None:
    assert _shadow_anemia(None, None).motivo == "sin valor de hemoglobina"

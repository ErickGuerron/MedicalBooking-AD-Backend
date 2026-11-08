"""Shadow mode (feature doc S13): findings shown to the physician, never changing the decision.

Pure bundle of the three findings for one set of inputs. The report is marked as shadow and states
that it does not modify the order, the result or the diagnosis record.
An unknown anaemia status excludes HbA1c, the conservative choice (ADA 2026, recommendation 2.4).
"""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.anemia import (
    ANEMIA_MODERADA,
    NO_EVALUABLE,
    SIN_ANEMIA,
)
from medicalbooking.modules.clinical.domain.diabetes import PENDIENTE_CONFIRMACION
from medicalbooking.modules.clinical.domain.iron import SIN_DEFICIENCIA
from medicalbooking.modules.clinical.domain.shadow import ShadowInputs, shadow_report


def d(value: str) -> Decimal:
    return Decimal(value)


def ds(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


def _inputs(**overrides) -> ShadowInputs:
    base = {
        "sex": "M",
        "age_months": 40 * 12,
        "pregnancy": None,
        "hemoglobin_g_dl": d("14.0"),
        "ferritin_ug_l": d("80.0"),
        "crp_mg_l": d("1.0"),
        "fasting_mg_dl": [],
        "hba1c_pct": [],
        "ogtt_2h_mg_dl": [],
        "random_mg_dl": None,
        "symptoms": None,
    }
    base.update(overrides)
    return ShadowInputs(**base)


def test_report_is_marked_as_shadow_and_does_not_modify_the_decision() -> None:
    report = shadow_report(_inputs())

    assert report.modo == "SOMBRA"
    assert report.modifica_decision is False


def test_report_carries_the_three_findings() -> None:
    report = shadow_report(_inputs())

    assert report.anemia.categoria == SIN_ANEMIA
    assert report.hierro.categoria == SIN_DEFICIENCIA
    assert report.diabetes.categoria == NO_EVALUABLE
    assert "sin glucosa" in report.diabetes.motivo


def test_known_anaemia_excludes_hba1c_from_diabetes() -> None:
    report = shadow_report(
        _inputs(hemoglobin_g_dl=d("10.0"), fasting_mg_dl=ds("130"), hba1c_pct=ds("7.0"))
    )

    assert report.anemia.categoria == ANEMIA_MODERADA
    assert report.diabetes.categoria == PENDIENTE_CONFIRMACION
    assert "HbA1c no valida" in report.diabetes.motivo


def test_unknown_anaemia_status_also_excludes_hba1c() -> None:
    report = shadow_report(_inputs(hemoglobin_g_dl=None, hba1c_pct=ds("7.0", "7.2")))

    assert report.anemia.categoria == NO_EVALUABLE
    assert report.diabetes.categoria == NO_EVALUABLE
    assert "HbA1c no valida" in report.diabetes.motivo


def test_normal_anaemia_keeps_hba1c_for_diabetes() -> None:
    report = shadow_report(_inputs(hba1c_pct=ds("7.0", "7.2")))

    assert report.anemia.categoria == SIN_ANEMIA
    assert report.diabetes.categoria == "DIABETES_CONFIRMADA"

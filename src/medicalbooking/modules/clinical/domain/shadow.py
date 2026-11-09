"""Shadow mode (feature doc S13). Pure function.

The three findings for one set of inputs, labelled as shadow. The report never modifies the order,
the result or the diagnosis record: it is shown to the physician alongside the normal flow.
An anaemia status that is not "no anaemia" (including unknown) excludes HbA1c from diabetes,
the conservative choice (ADA 2026, recommendation 2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from medicalbooking.modules.clinical.domain.anemia import (
    NO_EVALUABLE,
    SIN_ANEMIA,
    AnemiaFinding,
    anemia_finding,
)
from medicalbooking.modules.clinical.domain.diabetes import DiabetesFinding, diabetes_finding
from medicalbooking.modules.clinical.domain.iron import IronFinding, iron_finding

MODO_SOMBRA = "SOMBRA"


@dataclass(frozen=True)
class ShadowInputs:
    sex: str | None
    age_months: int | None
    pregnancy: str | None
    hemoglobin_g_dl: Decimal | None
    ferritin_ug_l: Decimal | None
    crp_mg_l: Decimal | None
    fasting_mg_dl: list[Decimal] = field(default_factory=list)
    hba1c_pct: list[Decimal] = field(default_factory=list)
    ogtt_2h_mg_dl: list[Decimal] = field(default_factory=list)
    random_mg_dl: Decimal | None = None
    symptoms: bool | None = None
    # WHO 2024 altitude plus smoking adjustment in g/L. None means it is unknown: the anaemia
    # finding is then not evaluable rather than assumed to be zero (S3, S12).
    ajuste_g_l: int | None = 0


@dataclass(frozen=True)
class ShadowReport:
    anemia: AnemiaFinding
    hierro: IronFinding
    diabetes: DiabetesFinding
    modo: str = MODO_SOMBRA
    modifica_decision: bool = False


def shadow_report(inputs: ShadowInputs) -> ShadowReport:
    if inputs.ajuste_g_l is None:
        anemia = AnemiaFinding(
            NO_EVALUABLE, None, "falta altitud o tabaquismo para ajustar la hemoglobina"
        )
    else:
        anemia = anemia_finding(
            inputs.hemoglobin_g_dl,
            inputs.sex,
            inputs.age_months,
            inputs.pregnancy,
            ajuste_g_l=inputs.ajuste_g_l,
        )
    hierro = iron_finding(
        inputs.ferritin_ug_l, inputs.crp_mg_l, inputs.sex, inputs.age_months, inputs.pregnancy
    )
    diabetes = diabetes_finding(
        inputs.fasting_mg_dl,
        inputs.hba1c_pct,
        inputs.ogtt_2h_mg_dl,
        inputs.random_mg_dl,
        inputs.symptoms,
        anemia=anemia.categoria != SIN_ANEMIA,
    )
    return ShadowReport(anemia=anemia, hierro=hierro, diabetes=diabetes)

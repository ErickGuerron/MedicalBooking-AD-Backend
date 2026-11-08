"""Iron deficiency finding (clinical findings S5, S3). Pure functions.

Source: WHO 2020 guideline on use of ferritin concentrations to assess iron status in individuals
and populations (ISBN 978-92-4-000012-4), Table 1 and recommendation 1.2 / 4.2.
- Apparently healthy: deficiency below 12 ug/L (0 to 59 months) or 15 ug/L (5 years and older).
- With inflammation (CRP > 5 mg/L): higher cut-offs of 30 ug/L (0 to 59 months) or 70 ug/L (older),
  a conditional recommendation with low certainty of evidence.
- Pregnancy: only the first trimester has a cut-off (15 ug/L); other trimesters are not evaluable.
A suggestion for physician review, never a diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DEFICIENCIA_HIERRO = "DEFICIENCIA_HIERRO"
SIN_DEFICIENCIA = "SIN_DEFICIENCIA"
DEFICIENCIA_HIERRO_CON_INFLAMACION = "DEFICIENCIA_HIERRO_CON_INFLAMACION"
SIN_DEFICIENCIA_CON_INFLAMACION = "SIN_DEFICIENCIA_CON_INFLAMACION"
NO_EVALUABLE = "NO_EVALUABLE"

NO_PREGNANT = "NO"
_CRP_LIMIT_MG_L = Decimal("5")
_MONTHS_UNDER_5 = 60
_MONTHS_PREGNANCY_FROM = 10 * 12
_MONTHS_PREGNANCY_TO = 59 * 12 + 11


@dataclass(frozen=True)
class _Cutoffs:
    healthy: Decimal
    inflammation: Decimal


_UNDER_5 = _Cutoffs(Decimal("12"), Decimal("30"))
_FIVE_AND_OLDER = _Cutoffs(Decimal("15"), Decimal("70"))
_PREGNANT_FIRST_TRIMESTER = Decimal("15")


@dataclass(frozen=True)
class IronFinding:
    categoria: str
    corte_ug_l: Decimal | None
    motivo: str | None = None


def _not_evaluable(motivo: str) -> IronFinding:
    return IronFinding(NO_EVALUABLE, None, motivo)


def _pregnancy_gate(sex: str | None, age_months: int, pregnancy: str | None) -> str | None:
    """Return a not-evaluable reason, or None when the pregnancy state is usable."""
    if sex == "M":
        if pregnancy in ("T1", "T2", "T3"):
            return "embarazo no aplicable a hombres"
        return None
    if _MONTHS_PREGNANCY_FROM <= age_months <= _MONTHS_PREGNANCY_TO:
        if pregnancy is None:
            return "embarazo desconocido: se requiere estado de embarazo"
    return None


def iron_finding(
    ferritin_ug_l: Decimal | None,
    crp_mg_l: Decimal | None,
    sex: str | None,
    age_months: int | None,
    pregnancy: str | None,
) -> IronFinding:
    """Classify ferritin (ug/L) with CRP (mg/L) using WHO 2020 Table 1 by age group."""
    if ferritin_ug_l is None:
        return _not_evaluable("sin ferritina")
    if crp_mg_l is None:
        return _not_evaluable("sin PCR: no se puede interpretar la ferritina")
    if age_months is None:
        return _not_evaluable("sin edad")

    gate = _pregnancy_gate(sex, age_months, pregnancy)
    if gate is not None:
        return _not_evaluable(gate)

    pregnant_first = pregnancy == "T1" and sex == "F"
    if pregnancy in ("T2", "T3") and sex == "F":
        return _not_evaluable("embarazo: solo se define corte para el primer trimestre")

    if pregnant_first:
        if crp_mg_l > _CRP_LIMIT_MG_L:
            return _not_evaluable("inflamacion en embarazo: sin corte definido")
        cutoff = _PREGNANT_FIRST_TRIMESTER
        if ferritin_ug_l < cutoff:
            return IronFinding(DEFICIENCIA_HIERRO, cutoff)
        return IronFinding(SIN_DEFICIENCIA, cutoff)

    groups = _UNDER_5 if age_months < _MONTHS_UNDER_5 else _FIVE_AND_OLDER
    if crp_mg_l > _CRP_LIMIT_MG_L:
        cutoff = groups.inflammation
        if ferritin_ug_l < cutoff:
            return IronFinding(DEFICIENCIA_HIERRO_CON_INFLAMACION, cutoff)
        return IronFinding(SIN_DEFICIENCIA_CON_INFLAMACION, cutoff)
    cutoff = groups.healthy
    if ferritin_ug_l < cutoff:
        return IronFinding(DEFICIENCIA_HIERRO, cutoff)
    return IronFinding(SIN_DEFICIENCIA, cutoff)

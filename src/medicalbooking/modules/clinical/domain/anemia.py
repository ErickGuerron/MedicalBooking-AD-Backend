"""Anaemia finding (clinical findings S2, S3, S6): WHO 2024 Tables 2 and 3. Pure functions.

Source: WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations
(2024), ISBN 978-92-4-008854-2, Tables 2 and 3. Ages 15 to 65 years in the table.

Rules that keep the finding honest:
- A missing input is never a category: the result is NO_EVALUABLE with a reason.
- Women aged 15 to 65 need a known pregnancy status (not pregnant, or trimester 1, 2 or 3).
- Under 6 months and over 65 years are outside the WHO 2024 table.
This is a suggestion for physician review, never a diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

SIN_ANEMIA = "SIN_ANEMIA"
ANEMIA_LEVE = "ANEMIA_LEVE"
ANEMIA_MODERADA = "ANEMIA_MODERADA"
ANEMIA_GRAVE = "ANEMIA_GRAVE"
NO_EVALUABLE = "NO_EVALUABLE"

NO_PREGNANT = "NO"
_TRIMESTERS = ("T1", "T2", "T3")
_LAST_ADULT_MONTH = 65 * 12 + 11  # 65 years, 11 months: the WHO table ends at 65 years


@dataclass(frozen=True)
class _Band:
    cutoff: int  # g/L: value at or above this is no anaemia
    mild_from: int  # g/L: mild anaemia from here (up to cutoff - 1)
    moderate_from: int  # g/L: moderate anaemia from here; below it is severe


@dataclass(frozen=True)
class AnemiaFinding:
    categoria: str
    corte_g_l: int | None
    motivo: str | None = None


_INFANT_6_23 = _Band(105, 95, 70)
_CHILD_24_59 = _Band(110, 100, 70)
_CHILD_5_11 = _Band(115, 110, 80)
_TEEN_12_14 = _Band(120, 110, 80)
_WOMAN_NOT_PREGNANT = _Band(120, 110, 80)
_MAN = _Band(130, 110, 80)
_PREGNANT = {"T1": _Band(110, 100, 70), "T2": _Band(105, 95, 70), "T3": _Band(110, 100, 70)}


def _not_evaluable(motivo: str) -> AnemiaFinding:
    return AnemiaFinding(NO_EVALUABLE, None, motivo)


def _band_for(age_months: int, sex: str | None, pregnancy: str | None) -> _Band | AnemiaFinding:
    if age_months < 6:
        return _not_evaluable("menor de 6 meses: la OMS 2024 no define corte")
    if age_months <= 23:
        return _INFANT_6_23
    if age_months <= 59:
        return _CHILD_24_59
    if age_months <= 143:
        return _CHILD_5_11
    if age_months <= 179:
        return _TEEN_12_14
    if age_months > _LAST_ADULT_MONTH:
        return _not_evaluable("mayor de 65 anos: fuera de la tabla OMS 2024")
    if sex is None:
        return _not_evaluable("sexo desconocido: el corte de adultos depende del sexo")
    if sex == "M":
        if pregnancy in _TRIMESTERS:
            return _not_evaluable("embarazo no aplicable a hombres")
        return _MAN
    if sex != "F":
        return _not_evaluable("sexo no reconocido")
    if pregnancy is None:
        return _not_evaluable("embarazo desconocido: se requiere estado de embarazo")
    if pregnancy == NO_PREGNANT:
        return _WOMAN_NOT_PREGNANT
    if pregnancy in _TRIMESTERS:
        return _PREGNANT[pregnancy]
    return _not_evaluable("estado de embarazo no reconocido")


def anemia_finding(
    hemoglobin_g_dl: Decimal | None,
    sex: str | None,
    age_months: int | None,
    pregnancy: str | None,
    ajuste_g_l: int = 0,
) -> AnemiaFinding:
    """Classify haemoglobin (g/dL) against the WHO 2024 cut-off for the patient.

    age_months is the age in completed months (the WHO table mixes months and years).
    ajuste_g_l is the WHO 2024 altitude plus smoking adjustment, subtracted from the observed value
    (Tables 4 and 5). The caller decides whether it is known; 0 means no adjustment applies.
    """
    if hemoglobin_g_dl is None:
        return _not_evaluable("sin valor de hemoglobina")
    if age_months is None:
        return _not_evaluable("sin edad")
    band = _band_for(age_months, sex, pregnancy)
    if isinstance(band, AnemiaFinding):
        return band
    value_g_l = hemoglobin_g_dl * 10 - ajuste_g_l
    if value_g_l >= band.cutoff:
        categoria = SIN_ANEMIA
    elif value_g_l >= band.mild_from:
        categoria = ANEMIA_LEVE
    elif value_g_l >= band.moderate_from:
        categoria = ANEMIA_MODERADA
    else:
        categoria = ANEMIA_GRAVE
    return AnemiaFinding(categoria, band.cutoff)

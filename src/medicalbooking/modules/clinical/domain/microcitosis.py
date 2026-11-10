"""Supporting MCV signal for iron deficiency (feature doc S20). Pure function.

Source: Fletcher A, et al. Guideline for the laboratory diagnosis of iron deficiency in adults
(excluding pregnancy) and children. Br J Haematol 2022;196:523-529, DOI 10.1111/bjh.17900:
"If thalassaemia is not present, then any of MCV, MCHC or MCH below the normal range could suggest
iron deficiency" (grade 2C). Normal indices do not exclude iron deficiency (grade 1B).

The limit is the laboratory's own validated reference for MCV, passed in by the caller. It is never
a number in code: without it the signal is NO_EVALUABLE. The signal only accompanies an anaemia
finding. It is a suggestion for physician review, never a diagnosis, and it never produces a
"no deficiency" category.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from medicalbooking.modules.clinical.domain.anemia import (
    ANEMIA_GRAVE,
    ANEMIA_LEVE,
    ANEMIA_MODERADA,
)
from medicalbooking.modules.clinical.domain.anemia import (
    NO_EVALUABLE as ANEMIA_NO_EVALUABLE,
)
from medicalbooking.modules.clinical.domain.references import Reference

SENAL_MICROCITOSIS = "SENAL_MICROCITOSIS"
SIN_SENAL = "SIN_SENAL"
NO_EVALUABLE = "NO_EVALUABLE"

FUENTE = "BSH 2022 (Fletcher et al.), recomendacion sobre indices eritrocitarios, grado 2C"
AVISO = "Puede deberse a talasemia u otra causa. Un VCM normal no descarta deficiencia de hierro."

_CON_ANEMIA = frozenset({ANEMIA_LEVE, ANEMIA_MODERADA, ANEMIA_GRAVE})
_UNIDAD_VCM = "fL"
_DIAS_ADULTO = 18 * 365 + 4  # 18 years in days, leap days included
_SEXOS = frozenset({"F", "M"})


@dataclass(frozen=True)
class MicrocitosisFinding:
    categoria: str
    limite_inferior_fl: Decimal | None = None
    motivo: str | None = None
    aviso: str | None = None
    fuente: str = FUENTE


def _not_evaluable(motivo: str) -> MicrocitosisFinding:
    return MicrocitosisFinding(NO_EVALUABLE, None, motivo)


def microcitosis_signal(
    anemia_categoria: str,
    vcm_fl: Decimal | None,
    limite_inferior_fl: Decimal | None,
) -> MicrocitosisFinding:
    """Signal when the MCV is below the laboratory's lower reference limit, with anaemia present."""
    if anemia_categoria == ANEMIA_NO_EVALUABLE:
        return _not_evaluable("anemia no evaluable: la senal solo acompana a la anemia")
    if anemia_categoria not in _CON_ANEMIA:
        return _not_evaluable("sin anemia: la senal solo acompana a la anemia")
    if vcm_fl is None:
        return _not_evaluable("sin VCM")
    if limite_inferior_fl is None:
        return _not_evaluable("sin rango de referencia validado de VCM del laboratorio")
    if vcm_fl < limite_inferior_fl:
        return MicrocitosisFinding(SENAL_MICROCITOSIS, limite_inferior_fl, None, AVISO)
    return MicrocitosisFinding(SIN_SENAL, limite_inferior_fl)


def limite_inferior_vcm(referencias: Iterable[Reference], sexo: str | None) -> Decimal | None:
    """Lower MCV limit from the validated references of the laboratory, or None (S20).

    A reference is used only when it is unrestricted (no context, sample type or method), covers
    adult ages, is in fL, carries a lower limit and applies to the patient's sex. Exactly one such
    reference must exist; zero or several give None, so the signal stays NO_EVALUABLE (S2).
    """
    if sexo not in _SEXOS:
        return None
    aplicables = [
        r
        for r in referencias
        if r.unidad == _UNIDAD_VCM
        and r.minimo is not None
        and r.sexo in (None, sexo)
        and r.contexto is None
        and r.tipo_muestra is None
        and r.metodo is None
        and (r.edad_min_dias is None or r.edad_min_dias <= _DIAS_ADULTO)
        and (r.edad_max_dias is None or r.edad_max_dias >= _DIAS_ADULTO)
    ]
    if len(aplicables) != 1:
        return None
    return aplicables[0].minimo

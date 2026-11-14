"""Supporting MCV signal for iron deficiency (feature doc S20). Written test-first."""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.anemia import (
    ANEMIA_LEVE,
    SIN_ANEMIA,
)
from medicalbooking.modules.clinical.domain.anemia import (
    NO_EVALUABLE as ANEMIA_NO_EVALUABLE,
)
from medicalbooking.modules.clinical.domain.microcitosis import (
    NO_EVALUABLE,
    SENAL_MICROCITOSIS,
    SIN_SENAL,
    microcitosis_signal,
)


def d(value: str) -> Decimal:
    return Decimal(value)


def test_low_mcv_below_the_laboratory_limit_with_anaemia_gives_the_signal() -> None:
    finding = microcitosis_signal(ANEMIA_LEVE, d("72"), d("80"))

    assert finding.categoria == SENAL_MICROCITOSIS
    assert finding.limite_inferior_fl == d("80")
    assert finding.aviso is not None and "talasemia" in finding.aviso


def test_mcv_equal_to_the_limit_is_inside_the_reference_range() -> None:
    assert microcitosis_signal(ANEMIA_LEVE, d("80"), d("80")).categoria == SIN_SENAL


def test_normal_mcv_gives_no_signal_and_is_never_a_no_deficiency_finding() -> None:
    finding = microcitosis_signal(ANEMIA_LEVE, d("92"), d("80"))

    assert finding.categoria == SIN_SENAL
    assert finding.aviso is None


def test_without_anaemia_the_signal_does_not_apply() -> None:
    finding = microcitosis_signal(SIN_ANEMIA, d("65"), d("80"))

    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo is not None and "sin anemia" in finding.motivo


def test_unknown_anaemia_is_not_evaluable() -> None:
    finding = microcitosis_signal(ANEMIA_NO_EVALUABLE, d("65"), d("80"))

    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo is not None and "anemia no evaluable" in finding.motivo


def test_missing_mcv_is_not_evaluable_with_a_reason() -> None:
    finding = microcitosis_signal(ANEMIA_LEVE, None, d("80"))

    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo == "sin VCM"


def test_without_a_validated_laboratory_limit_nothing_is_decided() -> None:
    # S20: the limit comes from the laboratory's validated reference, never from a number in code.
    finding = microcitosis_signal(ANEMIA_LEVE, d("60"), None)

    assert finding.categoria == NO_EVALUABLE
    assert finding.motivo is not None and "rango de referencia validado" in finding.motivo


def test_the_source_is_the_bsh_recommendation_with_its_grade() -> None:
    finding = microcitosis_signal(ANEMIA_LEVE, d("72"), d("80"))

    assert "BSH" in finding.fuente and "2C" in finding.fuente

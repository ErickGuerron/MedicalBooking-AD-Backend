"""Reference ranges: classification, applicability and overlap rules (E5). Pure domain, no I/O."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.references import (
    PatientContext,
    Reference,
    classify,
    overlaps,
)

TODAY = date(2026, 11, 2)


def _ref(**overrides) -> Reference:
    values = {
        "id": "r-1",
        "version": 1,
        "unidad": "g/dL",
        "minimo": Decimal("12.0"),
        "maximo": Decimal("17.5"),
        "sexo": None,
        "edad_min_dias": None,
        "edad_max_dias": None,
        "metodo": None,
        "tipo_muestra": None,
        "contexto": None,
        "fecha_inicio": date(2020, 1, 1),
        "fecha_fin": None,
    }
    values.update(overrides)
    return Reference(**values)


ANYONE = PatientContext(sexo=None, edad_dias=None)


# ---------- classification ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("11.99", "BAJO"),
        ("12.0", "DENTRO_REFERENCIA"),
        ("15.0", "DENTRO_REFERENCIA"),
        ("17.5", "DENTRO_REFERENCIA"),
        ("17.51", "ALTO"),
    ],
)
def test_boundaries_are_inclusive(value, expected) -> None:
    result = classify(Decimal(value), "g/dL", [_ref()], ANYONE, TODAY)

    assert result.interpretacion == expected
    assert result.reference is not None


def test_open_bounds_are_unbounded() -> None:
    only_min = _ref(maximo=None)

    assert classify(Decimal("999"), "g/dL", [only_min], ANYONE, TODAY).interpretacion == (
        "DENTRO_REFERENCIA"
    )


@pytest.mark.parametrize(
    ("value", "unit", "refs", "today", "reason"),
    [
        (None, "g/dL", [_ref()], TODAY, "no numeric value"),
        (Decimal("15"), None, [_ref()], TODAY, "unit missing"),
        (Decimal("15"), "mmol/L", [_ref()], TODAY, "unit does not match any reference"),
        (Decimal("15"), "g/dL", [], TODAY, "no reference"),
        (Decimal("15"), "g/dL", [_ref(fecha_fin=date(2025, 12, 31))], TODAY, "reference expired"),
        (Decimal("15"), "g/dL", [_ref(fecha_inicio=date(2027, 1, 1))], TODAY, "not yet valid"),
    ],
)
def test_no_category_is_invented_when_the_result_cannot_be_classified(
    value, unit, refs, today, reason
) -> None:
    result = classify(value, unit, refs, ANYONE, today)

    assert result.interpretacion == "NO_EVALUABLE", reason
    assert result.reference is None


def test_sex_specific_reference_needs_the_patient_sex() -> None:
    female_only = _ref(sexo="F", minimo=Decimal("12.0"), maximo=Decimal("15.5"))

    unknown = classify(Decimal("14"), "g/dL", [female_only], ANYONE, TODAY)
    known = classify(Decimal("14"), "g/dL", [female_only], PatientContext("F", None), TODAY)
    other = classify(Decimal("14"), "g/dL", [female_only], PatientContext("M", None), TODAY)

    assert unknown.interpretacion == "NO_EVALUABLE"
    assert known.interpretacion == "DENTRO_REFERENCIA"
    assert other.interpretacion == "NO_EVALUABLE"


def test_age_bounded_reference_needs_the_patient_age() -> None:
    adults = _ref(edad_min_dias=18 * 365, edad_max_dias=None)

    assert classify(Decimal("14"), "g/dL", [adults], ANYONE, TODAY).interpretacion == "NO_EVALUABLE"
    assert (
        classify(
            Decimal("14"), "g/dL", [adults], PatientContext(None, 20 * 365), TODAY
        ).interpretacion
        == "DENTRO_REFERENCIA"
    )
    assert (
        classify(
            Decimal("14"), "g/dL", [adults], PatientContext(None, 10 * 365), TODAY
        ).interpretacion
        == "NO_EVALUABLE"
    )


def test_reference_requiring_an_unknown_context_is_not_guessed() -> None:
    fasting = _ref(contexto="AYUNO")

    assert classify(Decimal("14"), "g/dL", [fasting], ANYONE, TODAY).interpretacion == (
        "NO_EVALUABLE"
    )


def test_two_applicable_references_are_ambiguous_and_not_chosen() -> None:
    first = _ref(id="r-a")
    second = _ref(id="r-b", minimo=Decimal("10"), maximo=Decimal("20"))

    result = classify(Decimal("14"), "g/dL", [first, second], ANYONE, TODAY)

    assert result.interpretacion == "NO_EVALUABLE"


def test_a_definite_reference_wins_over_one_that_does_not_apply() -> None:
    female = _ref(id="r-f", sexo="F")
    general = _ref(id="r-g", sexo="M", minimo=Decimal("13"), maximo=Decimal("17"))

    result = classify(Decimal("14"), "g/dL", [female, general], PatientContext("F", None), TODAY)

    assert result.interpretacion == "DENTRO_REFERENCIA"
    assert result.reference.id == "r-f"


# ---------- overlap (wildcards: None applies to everyone) ----------


def test_identical_references_overlap() -> None:
    assert overlaps(_ref(id="a"), _ref(id="b"))


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Each pair differs in exactly one dimension, so only that dimension keeps them apart.
        (_ref(id="a"), _ref(id="b", unidad="mmol/L")),
        (_ref(id="a", sexo="M"), _ref(id="b", sexo="F")),
        (
            _ref(id="a", edad_min_dias=0, edad_max_dias=365 * 10),
            _ref(id="b", edad_min_dias=365 * 20, edad_max_dias=None),
        ),
        (
            _ref(id="a", fecha_inicio=date(2020, 1, 1), fecha_fin=date(2025, 12, 31)),
            _ref(id="b", fecha_inicio=date(2030, 1, 1), fecha_fin=None),
        ),
        (_ref(id="a", metodo="ELISA"), _ref(id="b", metodo="HPLC")),
    ],
)
def test_references_separated_in_one_dimension_do_not_overlap(left, right) -> None:
    assert not overlaps(left, right)
    assert not overlaps(right, left)


def test_a_sex_neutral_reference_overlaps_a_sex_specific_one() -> None:
    assert overlaps(_ref(id="a"), _ref(id="b", sexo="F"))


def test_open_ended_validity_overlaps_later_validity() -> None:
    open_ended = _ref(id="a", fecha_inicio=date(2020, 1, 1), fecha_fin=None)
    later = _ref(id="b", fecha_inicio=date(2029, 1, 1), fecha_fin=date(2029, 12, 31))

    assert overlaps(open_ended, later)


def test_closed_validity_that_ends_before_the_other_starts_does_not_overlap() -> None:
    old = _ref(id="a", fecha_inicio=date(2020, 1, 1), fecha_fin=date(2021, 12, 31))
    new = _ref(id="b", fecha_inicio=date(2022, 1, 1), fecha_fin=None)

    assert not overlaps(old, new)

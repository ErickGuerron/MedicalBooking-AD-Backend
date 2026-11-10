"""Lower MCV limit read from a validated laboratory reference (feature doc S20). Test-first."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from medicalbooking.modules.clinical.domain.microcitosis import limite_inferior_vcm
from medicalbooking.modules.clinical.domain.references import Reference


def ref(
    *,
    minimo: str | None = "80",
    unidad: str = "fL",
    sexo: str | None = None,
    edad_min_dias: int | None = None,
    edad_max_dias: int | None = None,
    contexto: str | None = None,
    tipo_muestra: str | None = None,
    metodo: str | None = None,
    id_: str = "r1",
) -> Reference:
    return Reference(
        id=id_,
        version=1,
        unidad=unidad,
        minimo=None if minimo is None else Decimal(minimo),
        maximo=Decimal("100"),
        sexo=sexo,
        edad_min_dias=edad_min_dias,
        edad_max_dias=edad_max_dias,
        metodo=metodo,
        tipo_muestra=tipo_muestra,
        contexto=contexto,
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=None,
    )


def test_one_unrestricted_reference_applies_to_both_sexes() -> None:
    refs = [ref(minimo="80")]

    assert limite_inferior_vcm(refs, "F") == Decimal("80")
    assert limite_inferior_vcm(refs, "M") == Decimal("80")


def test_a_sex_specific_reference_applies_only_to_its_sex() -> None:
    refs = [ref(minimo="78", sexo="F", id_="f"), ref(minimo="82", sexo="M", id_="m")]

    assert limite_inferior_vcm(refs, "F") == Decimal("78")
    assert limite_inferior_vcm(refs, "M") == Decimal("82")


def test_no_applicable_reference_gives_none() -> None:
    assert limite_inferior_vcm([], "F") is None
    assert limite_inferior_vcm([ref(sexo="M")], "F") is None


def test_two_applicable_references_are_ambiguous_and_give_none() -> None:
    refs = [ref(minimo="80", id_="a"), ref(minimo="78", id_="b")]

    assert limite_inferior_vcm(refs, "F") is None


def test_a_reference_restricted_by_context_is_not_used() -> None:
    assert limite_inferior_vcm([ref(contexto="EMBARAZO")], "F") is None
    assert limite_inferior_vcm([ref(tipo_muestra="CAPILAR")], "F") is None
    assert limite_inferior_vcm([ref(metodo="IMPEDANCIA")], "F") is None


def test_a_reference_for_children_only_is_not_used_for_adults() -> None:
    assert limite_inferior_vcm([ref(edad_max_dias=3650)], "F") is None


def test_a_reference_in_another_unit_is_not_used() -> None:
    assert limite_inferior_vcm([ref(unidad="g/dL")], "F") is None


def test_a_reference_without_lower_limit_is_not_used() -> None:
    assert limite_inferior_vcm([ref(minimo=None)], "F") is None


def test_an_unknown_sex_gives_none() -> None:
    assert limite_inferior_vcm([ref()], None) is None

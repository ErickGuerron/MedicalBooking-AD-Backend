"""Treatment business rules (S4, S5): pure functions, no I/O."""

from __future__ import annotations

from datetime import date

import pytest

from medicalbooking.modules.treatments.domain.policies import (
    MAX_DESCRIPCION,
    MAX_NOTA,
    validate_descripcion,
    validate_estado,
    validate_fechas,
    validate_nota,
    validate_transition,
)
from medicalbooking.shared.domain.errors import ValidationFailed

INICIO = date(2026, 11, 2)


@pytest.mark.parametrize("estado", ["PAUSADO", "activo", ""])
def test_estado_must_be_a_known_state(estado) -> None:
    with pytest.raises(ValidationFailed):
        validate_estado(estado)


@pytest.mark.parametrize("estado", ["ACTIVO", "FINALIZADO", "SUSPENDIDO"])
def test_known_states_are_accepted(estado) -> None:
    validate_estado(estado)


@pytest.mark.parametrize("texto", ["", "   "])
def test_descripcion_cannot_be_blank(texto) -> None:
    with pytest.raises(ValidationFailed):
        validate_descripcion(texto)


def test_descripcion_length_limit_is_inclusive() -> None:
    validate_descripcion("x" * MAX_DESCRIPCION)
    with pytest.raises(ValidationFailed):
        validate_descripcion("x" * (MAX_DESCRIPCION + 1))


@pytest.mark.parametrize("texto", ["", "  \t "])
def test_nota_cannot_be_blank(texto) -> None:
    with pytest.raises(ValidationFailed):
        validate_nota(texto)


def test_nota_length_limit_is_inclusive() -> None:
    validate_nota("x" * MAX_NOTA)
    with pytest.raises(ValidationFailed):
        validate_nota("x" * (MAX_NOTA + 1))


def test_finalized_treatment_requires_an_end_date() -> None:
    with pytest.raises(ValidationFailed):
        validate_fechas(INICIO, None, "FINALIZADO")


def test_finalized_treatment_with_end_date_is_accepted() -> None:
    validate_fechas(INICIO, date(2026, 11, 9), "FINALIZADO")


@pytest.mark.parametrize("estado", ["ACTIVO", "SUSPENDIDO"])
def test_active_and_suspended_treatments_may_have_no_end_date(estado) -> None:
    validate_fechas(INICIO, None, estado)


def test_end_date_cannot_be_before_start_date() -> None:
    with pytest.raises(ValidationFailed):
        validate_fechas(INICIO, date(2026, 11, 1), "ACTIVO")


def test_end_date_equal_to_start_date_is_accepted() -> None:
    validate_fechas(INICIO, INICIO, "FINALIZADO")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("ACTIVO", "FINALIZADO"),
        ("ACTIVO", "SUSPENDIDO"),
        ("SUSPENDIDO", "ACTIVO"),
        ("SUSPENDIDO", "FINALIZADO"),
        ("ACTIVO", "ACTIVO"),
        ("FINALIZADO", "FINALIZADO"),
    ],
)
def test_allowed_state_changes(current, target) -> None:
    validate_transition(current, target)


@pytest.mark.parametrize("target", ["ACTIVO", "SUSPENDIDO"])
def test_finalized_is_terminal(target) -> None:
    with pytest.raises(ValidationFailed):
        validate_transition("FINALIZADO", target)

"""Physician edits of a shadow finding (clinical findings S7, S14). Pure rules.

A physician confirms, discards or modifies one finding of an immutable snapshot. Every action needs
a reason. The stored value is the new category; the suggestion itself is never overwritten.
"""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.domain.shadow_edits import (
    DESCARTADO,
    resolver_edicion,
)
from medicalbooking.shared.domain.errors import ValidationFailed


def test_confirming_keeps_the_suggested_category() -> None:
    nueva = resolver_edicion("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, "coincide con la clinica")
    assert nueva == "ANEMIA_LEVE"


def test_discarding_stores_the_discarded_marker() -> None:
    nueva = resolver_edicion("HIERRO", "DESCARTAR", "DEFICIENCIA_HIERRO", None, "falso positivo")
    assert nueva == DESCARTADO


def test_modifying_requires_an_allowed_different_category() -> None:
    nueva = resolver_edicion(
        "ANEMIA", "MODIFICAR", "ANEMIA_LEVE", "SIN_ANEMIA", "hemoglobina corregida por altitud"
    )
    assert nueva == "SIN_ANEMIA"


@pytest.mark.parametrize(
    ("hallazgo", "accion", "actual", "nueva", "motivo"),
    [
        ("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, ""),  # no reason
        ("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, "   "),  # blank reason
        ("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", "SIN_ANEMIA", "x"),  # confirm must not change
        ("ANEMIA", "DESCARTAR", "ANEMIA_LEVE", "SIN_ANEMIA", "x"),  # discard takes no category
        ("ANEMIA", "MODIFICAR", "ANEMIA_LEVE", None, "x"),  # modify needs a category
        ("ANEMIA", "MODIFICAR", "ANEMIA_LEVE", "ANEMIA_LEVE", "x"),  # not a change
        ("ANEMIA", "MODIFICAR", "ANEMIA_LEVE", "DEFICIENCIA_HIERRO", "x"),  # other finding
        ("ANEMIA", "BORRAR", "ANEMIA_LEVE", None, "x"),  # unknown action
        ("RINON", "CONFIRMAR", "ANEMIA_LEVE", None, "x"),  # unknown finding
    ],
)
def test_invalid_edits_are_refused(hallazgo, accion, actual, nueva, motivo) -> None:
    with pytest.raises(ValidationFailed):
        resolver_edicion(hallazgo, accion, actual, nueva, motivo)


def test_reason_length_counts_utf16_units_as_the_database_does() -> None:
    # nvarchar(500) counts UTF-16 units: an emoji takes two, so 250 emoji fit and 251 do not.
    fits = "\U0001f600" * 250
    assert resolver_edicion("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, fits) == "ANEMIA_LEVE"
    with pytest.raises(ValidationFailed):
        resolver_edicion("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, "\U0001f600" * 251)


@pytest.mark.parametrize("motivo", ["\u200b", "\u200b \u200b", "\ufeff", "\u00a0"])
def test_a_reason_of_invisible_characters_only_is_blank(motivo: str) -> None:
    with pytest.raises(ValidationFailed):
        resolver_edicion("ANEMIA", "CONFIRMAR", "ANEMIA_LEVE", None, motivo)

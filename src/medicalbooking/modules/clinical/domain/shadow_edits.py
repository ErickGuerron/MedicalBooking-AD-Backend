"""Physician edits of a shadow finding (clinical findings S7, S14). Pure rules.

- CONFIRMAR keeps the suggested category; the new value equals the suggestion.
- DESCARTAR stores the marker DESCARTADO; no category is given.
- MODIFICAR stores an allowed category of the same finding, different from the suggestion.
Every action needs a non-blank reason. The suggestion itself is never overwritten.
"""

from __future__ import annotations

import unicodedata

from medicalbooking.shared.domain.errors import ValidationFailed

CONFIRMAR = "CONFIRMAR"
DESCARTAR = "DESCARTAR"
MODIFICAR = "MODIFICAR"
DESCARTADO = "DESCARTADO"
MAX_MOTIVO = 500

CATEGORIAS_PERMITIDAS: dict[str, frozenset[str]] = {
    "ANEMIA": frozenset({"SIN_ANEMIA", "ANEMIA_LEVE", "ANEMIA_MODERADA", "ANEMIA_GRAVE"}),
    "HIERRO": frozenset(
        {
            "DEFICIENCIA_HIERRO",
            "SIN_DEFICIENCIA",
            "DEFICIENCIA_HIERRO_CON_INFLAMACION",
            "SIN_DEFICIENCIA_CON_INFLAMACION",
        }
    ),
    "DIABETES": frozenset(
        {"DIABETES_CONFIRMADA", "PENDIENTE_CONFIRMACION", "PREDIABETES", "SIN_EVIDENCIA_DIABETES"}
    ),
}


def _es_vacio(texto: str) -> bool:
    """Blank for a reason: only whitespace or invisible format characters (zero-width, BOM)."""
    return all(ch.isspace() or unicodedata.category(ch) == "Cf" for ch in texto)


def _longitud_sql(texto: str) -> int:
    """Length as nvarchar counts it: UTF-16 code units, so an emoji counts as two."""
    return len(texto.encode("utf-16-le")) // 2


def resolver_edicion(
    hallazgo: str,
    accion: str,
    categoria_actual: str,
    categoria_nueva: str | None,
    motivo: str | None,
) -> str:
    """Return the category to store for this edit, or raise ValidationFailed."""
    if hallazgo not in CATEGORIAS_PERMITIDAS:
        raise ValidationFailed("Unknown finding")
    if motivo is None or _es_vacio(motivo):
        raise ValidationFailed("A physician edit needs a reason")
    if _longitud_sql(motivo) > MAX_MOTIVO:
        raise ValidationFailed("Reason is too long")

    if accion == CONFIRMAR:
        if categoria_nueva is not None:
            raise ValidationFailed("Confirming keeps the suggested category; send no category")
        return categoria_actual
    if accion == DESCARTAR:
        if categoria_nueva is not None:
            raise ValidationFailed("Discarding takes no category")
        return DESCARTADO
    if accion == MODIFICAR:
        if categoria_nueva is None:
            raise ValidationFailed("Modifying needs the new category")
        if categoria_nueva not in CATEGORIAS_PERMITIDAS[hallazgo]:
            raise ValidationFailed("Category is not valid for this finding")
        if categoria_nueva == categoria_actual:
            raise ValidationFailed("The new category must differ from the suggestion")
        return categoria_nueva
    raise ValidationFailed("Action must be CONFIRMAR, DESCARTAR or MODIFICAR")

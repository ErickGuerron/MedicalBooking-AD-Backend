"""Canonical digest of the clinical knowledge base.

Two databases loaded from the same knowledge bundle must report the same digest, so the digest is
built from natural keys (LOINC codes, source codes, rule codes and versions) and never from row
identifiers, which differ between databases. Audit columns (who validated a row and when) are left
out: they describe how a row was approved, not the knowledge itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TableSpec:
    """One knowledge table: its natural key (sort order) and the canonical fields it exports."""

    name: str
    key: tuple[str, ...]
    fields: tuple[str, ...]


# The order is part of the digest: changing it changes every digest.
TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "fuentes",
        ("codigo",),
        (
            "codigo",
            "nombre",
            "organizacion",
            "url",
            "identificador_publicacion",
            "version_publicacion",
            "fecha_publicacion",
            "fecha_consulta",
            "licencia",
            "vigente",
        ),
    ),
    TableSpec(
        # codigo_loinc is NULL for some analytes, so the name completes the key.
        "analitos",
        ("codigo_loinc", "nombre"),
        (
            "codigo_loinc",
            "nombre",
            "descripcion",
            "unidad_canonica",
            "tipo_valor",
            "activo",
        ),
    ),
    TableSpec(
        # A reference is identified by its population and context, not by its version alone.
        "referencias",
        (
            "analito",
            "fuente",
            "version",
            "sexo_aplicable",
            "edad_min_dias",
            "edad_max_dias",
            "tipo_muestra",
            "metodo_analitico",
            "contexto",
        ),
        (
            "analito",
            "fuente",
            "version",
            "unidad",
            "sexo_aplicable",
            "edad_min_dias",
            "edad_max_dias",
            "tipo_muestra",
            "metodo_analitico",
            "contexto",
            "referencia_min",
            "referencia_max",
            "valor_texto_esperado",
            "fecha_inicio",
            "fecha_fin",
            "estado",
        ),
    ),
    TableSpec(
        "reglas",
        ("codigo", "version"),
        (
            "codigo",
            "version",
            "nombre",
            "descripcion",
            "tipo",
            "codigo_diagnostico",
            "expresion_logica",
            "mensaje_propuesto",
            "prioridad",
            "estado",
            "vigente_desde",
            "vigente_hasta",
        ),
    ),
    TableSpec(
        "reglas_criterios",
        ("regla", "version_regla", "orden_criterio"),
        (
            "regla",
            "version_regla",
            "orden_criterio",
            "analito",
            "operador",
            "umbral_numerico",
            "valor_codificado",
            "unidad",
            "contexto",
        ),
    ),
    TableSpec(
        "reglas_fuentes",
        ("regla", "version_regla", "fuente"),
        ("regla", "version_regla", "fuente"),
    ),
    TableSpec(
        "contenidos",
        ("analito", "fuente", "idioma", "tipo", "version", "texto"),
        (
            "analito",
            "fuente",
            "idioma",
            "tipo",
            "version",
            "texto",
            "url_referencia",
            "estado",
        ),
    ),
)


@dataclass(frozen=True)
class KnowledgeDigest:
    digest: str
    counts: Mapping[str, int]


def canonical_value(value: Any) -> Any:
    """Return a JSON-safe value with one spelling per logical value."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"unsupported knowledge value type: {type(value).__name__}")


def _sort_key(values: tuple[Any, ...]) -> tuple[tuple[int, str], ...]:
    # None sorts before any value, and mixed types never compare directly.
    return tuple((0, "") if v is None else (1, json.dumps(v, ensure_ascii=False)) for v in values)


def canonical_lines(spec: TableSpec, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """One JSON line per row, ordered by the natural key and then by the whole line.

    Rows that share a natural key are kept and counted: a real database can hold them (for
    example references that differ only in a field outside the key), and the digest must not fail
    on the database it is meant to check. Ordering by the full line keeps the result independent of
    the order the rows were read in.
    """
    prepared: list[tuple[tuple[tuple[int, str], ...], str]] = []
    for row in rows:
        missing = [f for f in spec.fields if f not in row]
        if missing:
            raise KeyError(f"{spec.name}: row is missing {', '.join(missing)}")
        values = {f: canonical_value(row[f]) for f in spec.fields}
        key = tuple(values[k] for k in spec.key)
        line = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        prepared.append((_sort_key(key), line))
    prepared.sort()
    return [line for _, line in prepared]


def knowledge_digest(tables: Mapping[str, Sequence[Mapping[str, Any]]]) -> KnowledgeDigest:
    """Digest of every knowledge table.

    A table absent from ``tables`` is refused, never assumed empty.
    """
    hasher = hashlib.sha256()
    counts: dict[str, int] = {}
    for spec in TABLES:
        if spec.name not in tables:
            raise KeyError(f"knowledge table missing from the export: {spec.name}")
        lines = canonical_lines(spec, tables[spec.name])
        counts[spec.name] = len(lines)
        hasher.update(f"#{spec.name}:{len(lines)}\n".encode())
        for line in lines:
            hasher.update(line.encode("utf-8") + b"\n")
    return KnowledgeDigest(digest=hasher.hexdigest(), counts=counts)

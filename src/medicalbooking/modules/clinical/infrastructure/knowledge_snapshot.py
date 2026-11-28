"""Read the clinical knowledge base as the canonical tables the digest is computed from.

Every table is read with its natural keys (LOINC codes, source codes, rule codes and versions), so
two databases holding the same knowledge produce the same snapshot, whatever their row ids are.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, text

from medicalbooking.modules.clinical.domain.knowledge_digest import (
    KnowledgeDigest,
    knowledge_digest,
)

_QUERIES: dict[str, str] = {
    "fuentes": (
        "SELECT codigo, nombre, organizacion, url, identificador_publicacion, "
        "version_publicacion, fecha_publicacion, fecha_consulta, licencia, vigente "
        "FROM conocimiento_fuentes"
    ),
    "analitos": (
        "SELECT codigo_loinc, nombre, descripcion, unidad_canonica, tipo_valor, activo "
        "FROM conocimiento_analitos"
    ),
    "referencias": (
        "SELECT a.codigo_loinc AS analito, f.codigo AS fuente, r.version, r.unidad, "
        "r.sexo_aplicable, r.edad_min_dias, r.edad_max_dias, r.tipo_muestra, "
        "r.metodo_analitico, r.contexto, r.referencia_min, r.referencia_max, "
        "r.valor_texto_esperado, r.fecha_inicio, r.fecha_fin, r.estado "
        "FROM conocimiento_referencias AS r "
        "JOIN conocimiento_analitos AS a ON a.id = r.analito_id "
        "JOIN conocimiento_fuentes AS f ON f.id = r.fuente_id"
    ),
    "reglas": (
        "SELECT codigo, version, nombre, descripcion, tipo, codigo_diagnostico, "
        "expresion_logica, mensaje_propuesto, prioridad, estado, vigente_desde, vigente_hasta "
        "FROM conocimiento_reglas"
    ),
    "reglas_criterios": (
        "SELECT r.codigo AS regla, r.version AS version_regla, c.orden_criterio, "
        "a.codigo_loinc AS analito, c.operador, c.umbral_numerico, c.valor_codificado, "
        "c.unidad, c.contexto "
        "FROM conocimiento_reglas_criterios AS c "
        "JOIN conocimiento_reglas AS r ON r.id = c.regla_id "
        "LEFT JOIN conocimiento_analitos AS a ON a.id = c.analito_id"
    ),
    "reglas_fuentes": (
        "SELECT r.codigo AS regla, r.version AS version_regla, f.codigo AS fuente "
        "FROM conocimiento_reglas_fuentes AS rf "
        "JOIN conocimiento_reglas AS r ON r.id = rf.regla_id "
        "JOIN conocimiento_fuentes AS f ON f.id = rf.fuente_id"
    ),
    "contenidos": (
        "SELECT a.codigo_loinc AS analito, f.codigo AS fuente, c.idioma, c.tipo, c.version, "
        "c.texto, c.url_referencia, c.estado "
        "FROM conocimiento_contenidos AS c "
        "JOIN conocimiento_analitos AS a ON a.id = c.analito_id "
        "JOIN conocimiento_fuentes AS f ON f.id = c.fuente_id"
    ),
}


def read_knowledge_tables(engine: Engine) -> dict[str, list[dict[str, Any]]]:
    """Every knowledge table, read in one connection, as rows keyed by canonical field names."""
    with engine.connect() as conn:
        return {
            name: [dict(row) for row in conn.execute(text(sql)).mappings().all()]
            for name, sql in _QUERIES.items()
        }


def database_knowledge_digest(engine: Engine) -> KnowledgeDigest:
    return knowledge_digest(read_knowledge_tables(engine))


def digest_provider(engine: Engine) -> Callable[[], str]:
    """The gate's view of the database: the digest of what it holds right now."""

    def current() -> str:
        return database_knowledge_digest(engine).digest

    return current

"""Writes a knowledge import plan in ONE transaction: all of it or nothing.

Any failure (an unknown rule, a constraint, a lost connection) raises inside the transaction
and SQL Server rolls back every earlier statement of the same run. Published knowledge is
never changed: codes are attached to drafts only, and only missing links are added.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from medicalbooking.modules.clinical.application.knowledge_import import ImportPlan


class ImportFailed(Exception):
    """A business check failed inside the transaction. The whole run is rolled back."""


def apply_plan(engine: Engine, plan: ImportPlan) -> dict[str, int]:
    counts = {
        "loinc_codes_set": 0,
        "loinc_codes_kept": 0,
        "sources_ensured": 0,
        "contents_added": 0,
        "rule_links_added": 0,
        "icd_codes_set": 0,
        "icd_skipped_published": 0,
    }
    with engine.begin() as conn:
        for item in plan.analyte_codes:
            set_now = conn.execute(
                text(
                    "UPDATE conocimiento_analitos SET codigo_loinc = :c, "
                    "actualizado_en = SYSUTCDATETIME() "
                    "WHERE id = :a AND codigo_loinc IS NULL"
                ),
                {"c": item.loinc, "a": item.analito_id},
            ).rowcount
            counts["loinc_codes_set"] += set_now
            if not set_now:
                # Already coded (or unknown id): never overwrite an existing terminology code.
                counts["loinc_codes_kept"] += 1

        for source in plan.sources:
            conn.execute(
                text(
                    "IF NOT EXISTS (SELECT 1 FROM conocimiento_fuentes WHERE codigo = :c) "
                    "INSERT INTO conocimiento_fuentes (id, codigo, nombre, organizacion, url, "
                    "identificador_publicacion, fecha_consulta, vigente) "
                    "VALUES (NEWID(), :c, :n, :o, :u, :i, SYSUTCDATETIME(), 1)"
                ),
                {
                    "c": source.codigo,
                    "n": source.nombre,
                    "o": source.organizacion,
                    "u": source.url,
                    "i": source.identificador,
                },
            )
            counts["sources_ensured"] += 1

        for content in plan.contents:
            counts["contents_added"] += conn.execute(
                text(
                    "INSERT INTO conocimiento_contenidos (id, analito_id, fuente_id, idioma, tipo, "
                    "texto, url_referencia, version, estado) "
                    "SELECT NEWID(), :a, f.id, 'es', 'EDUCACION', :titulo, :url, 1, 'BORRADOR' "
                    "FROM conocimiento_fuentes f WHERE f.codigo = 'MEDLINEPLUS' "
                    "AND NOT EXISTS (SELECT 1 FROM conocimiento_contenidos x "
                    "WHERE x.analito_id = :a AND x.url_referencia = :url)"
                ),
                {"a": content.analito_id, "titulo": content.title, "url": content.url},
            ).rowcount

        for link in plan.rule_sources:
            rule_id = conn.execute(
                text(
                    "SELECT TOP 1 id FROM conocimiento_reglas WHERE codigo = :c "
                    "ORDER BY version DESC"
                ),
                {"c": link.regla_codigo},
            ).scalar()
            if rule_id is None:
                raise ImportFailed(f"unknown rule {link.regla_codigo}")
            counts["rule_links_added"] += conn.execute(
                text(
                    "INSERT INTO conocimiento_reglas_fuentes (regla_id, fuente_id) "
                    "SELECT :r, f.id FROM conocimiento_fuentes f WHERE f.codigo = :s "
                    "AND NOT EXISTS (SELECT 1 FROM conocimiento_reglas_fuentes x "
                    "WHERE x.regla_id = :r AND x.fuente_id = f.id)"
                ),
                {"r": rule_id, "s": link.source_codigo},
            ).rowcount

        for diagnosis in plan.diagnoses:
            # Only drafts: published rules are immutable (a change is a new version).
            updated = conn.execute(
                text(
                    "UPDATE conocimiento_reglas SET codigo_diagnostico = :code "
                    "WHERE codigo = :c AND estado = 'BORRADOR'"
                ),
                {"code": diagnosis.code, "c": diagnosis.regla_codigo},
            ).rowcount
            if updated:
                counts["icd_codes_set"] += updated
            else:
                counts["icd_skipped_published"] += 1
    return counts

"""candidate-sources.sql gives the real analytes a test, so a result can be entered for them."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from medicalbooking.shared.infrastructure.tsql import split_batches

pytestmark = pytest.mark.integration

SQL_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "candidate-sources.sql"
GLUCOSE = "a1000000-0000-4000-8000-000000000001"
HEMOGLOBIN = "a1000000-0000-4000-8000-000000000002"

_LINKS = (
    "SELECT t.codigo, p.obligatorio FROM conocimiento_pruebas_analitos p "
    "JOIN tipos_prueba_laboratorio t ON t.id = p.tipo_prueba_id "
    "WHERE p.analito_id = :a"
)


def _apply(engine: Engine) -> None:
    with engine.begin() as conn:
        for batch in split_batches(SQL_FILE.read_text(encoding="utf-8")):
            conn.exec_driver_sql(batch)
        # The script switches NOCOUNT on. The pooled connection keeps it, so later
        # UPDATEs report rowcount -1 and rowcount checks in other tests break.
        conn.exec_driver_sql("SET NOCOUNT OFF")


def _count(engine: Engine, query: str, **params: str) -> int:
    with engine.connect() as conn:
        return len(conn.execute(text(query), params).all())


def test_each_real_analyte_belongs_to_one_mandatory_test(migrated_engine: Engine) -> None:
    _apply(migrated_engine)

    for analyte, code in ((GLUCOSE, "GLUC-AYUNO"), (HEMOGLOBIN, "HEMO-HGB")):
        with migrated_engine.connect() as conn:
            rows = conn.execute(text(_LINKS), {"a": analyte}).all()
        assert [(r.codigo, r.obligatorio) for r in rows] == [(code, True)]


def test_running_the_file_again_adds_nothing(migrated_engine: Engine) -> None:
    _apply(migrated_engine)
    before = _count(migrated_engine, "SELECT 1 FROM conocimiento_pruebas_analitos")
    tests_before = _count(migrated_engine, "SELECT 1 FROM tipos_prueba_laboratorio")

    _apply(migrated_engine)

    assert _count(migrated_engine, "SELECT 1 FROM conocimiento_pruebas_analitos") == before
    assert _count(migrated_engine, "SELECT 1 FROM tipos_prueba_laboratorio") == tests_before


def test_the_synthetic_tests_keep_only_their_synthetic_analytes(migrated_engine: Engine) -> None:
    _apply(migrated_engine)

    with migrated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.codigo, p.analito_id FROM conocimiento_pruebas_analitos p "
                "JOIN tipos_prueba_laboratorio t ON t.id = p.tipo_prueba_id "
                "WHERE t.codigo IN ('HEMO-SINT', 'GLUC-SINT')"
            )
        ).all()
    assert {str(r.analito_id).lower() for r in rows}.isdisjoint({GLUCOSE, HEMOGLOBIN})

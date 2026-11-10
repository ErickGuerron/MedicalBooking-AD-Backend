"""Reviewed reference batch (SQL Server): drafts citing their PMID, all or nothing."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine

from medicalbooking.modules.clinical.application.reference_csv import ReferenceRow
from medicalbooking.modules.clinical.infrastructure.external_sources import Article
from medicalbooking.modules.clinical.infrastructure.knowledge_import_repository import (
    ImportFailed,
    apply_reference_batch,
)
from tests.integration.test_scheduling_api import _count

pytestmark = pytest.mark.integration

HB = "a0000000-0000-4000-8000-00000000b001"  # seeded synthetic analyte


def _row(line: int, analito: str, pmid: str, unidad: str = "g/dL") -> ReferenceRow:
    return ReferenceRow(
        line=line,
        analito_id=analito,
        pmid=pmid,
        unidad=unidad,
        minimo=Decimal("12.0"),
        maximo=Decimal("17.5"),
        sexo=None,
        edad_min_dias=None,
        edad_max_dias=None,
        metodo=None,
        tipo_muestra=None,
        contexto=None,
        fecha_inicio=date(2015, 1, 1),
        fecha_fin=None,
    )


def _article(pmid: str) -> Article:
    return Article(pmid, "Test study", "Test journal", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")


def _drafts_for(engine: Engine, pmid: str) -> int:
    return _count(
        engine,
        "SELECT COUNT(*) FROM conocimiento_referencias r JOIN conocimiento_fuentes f "
        "ON f.id = r.fuente_id WHERE f.codigo = :c AND r.estado = 'BORRADOR'",
        c=f"PMID-{pmid}",
    )


def test_a_valid_batch_is_stored_as_drafts_citing_each_pmid(migrated_engine) -> None:
    written = apply_reference_batch(
        migrated_engine,
        (_row(2, HB, "90000001"),),
        {"90000001": _article("90000001")},
    )

    assert written == 1
    assert _drafts_for(migrated_engine, "90000001") == 1


def test_one_bad_row_rolls_back_the_whole_batch(migrated_engine) -> None:
    unknown = str(uuid.uuid4())
    before = _count(migrated_engine, "SELECT COUNT(*) FROM conocimiento_referencias")

    with pytest.raises(ImportFailed) as error:
        apply_reference_batch(
            migrated_engine,
            (_row(2, HB, "90000002"), _row(3, unknown, "90000002")),
            {"90000002": _article("90000002")},
        )

    assert "line 3" in str(error.value)
    assert _count(migrated_engine, "SELECT COUNT(*) FROM conocimiento_referencias") == before
    assert _drafts_for(migrated_engine, "90000002") == 0

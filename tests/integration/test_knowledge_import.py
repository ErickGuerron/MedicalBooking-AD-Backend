"""Knowledge import writes (SQL Server): one transaction, all or nothing, drafts only."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.clinical.application.knowledge_import import (
    AnalyteCode,
    ContentDraft,
    DiagnosisCode,
    ImportPlan,
    RuleSource,
    SourceRow,
)
from medicalbooking.modules.clinical.infrastructure.knowledge_import_repository import (
    ImportFailed,
    apply_plan,
)
from tests.integration.test_scheduling_api import _count

pytestmark = pytest.mark.integration

HB = "a0000000-0000-4000-8000-00000000b001"  # seeded hemoglobin (VALIDADA rule HB-BAJA-SINT)
GLU = "a0000000-0000-4000-8000-00000000b002"  # seeded glucose


def _codigo_loinc(engine: Engine, analito: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT codigo_loinc FROM conocimiento_analitos WHERE id = :a"), {"a": analito}
        ).scalar()


def test_a_complete_plan_is_written_and_published_rules_stay_untouched(migrated_engine) -> None:
    plan = ImportPlan(
        analyte_codes=(AnalyteCode(HB, "718-7"),),
        contents=(
            ContentDraft(HB, "718-7", "Hemoglobina", "https://medlineplus.gov/hb-test.html"),
        ),
        sources=(
            SourceRow(
                "MEDLINEPLUS", "MedlinePlus Connect", "NLM", "https://medlineplus.gov/", "MP"
            ),
        ),
        diagnoses=(DiagnosisCode("HB-BAJA-SINT", "3A9Z", "Anemia"),),
    )

    counts = apply_plan(migrated_engine, plan)

    assert _codigo_loinc(migrated_engine, HB) == "718-7"
    assert counts["contents_added"] == 1
    assert counts["icd_skipped_published"] == 1  # HB-BAJA-SINT is VALIDADA: never changed
    assert (
        _count(
            migrated_engine,
            "SELECT COUNT(*) FROM conocimiento_contenidos "
            "WHERE url_referencia = :u AND estado = 'BORRADOR'",
            u="https://medlineplus.gov/hb-test.html",
        )
        == 1
    )


def test_a_failure_halfway_rolls_back_every_earlier_write(migrated_engine) -> None:
    before_contents = _count(migrated_engine, "SELECT COUNT(*) FROM conocimiento_contenidos")
    assert _codigo_loinc(migrated_engine, GLU) is None
    plan = ImportPlan(
        analyte_codes=(AnalyteCode(GLU, "2339-0"),),
        contents=(ContentDraft(GLU, "2339-0", "Glucosa", "https://medlineplus.gov/glu-test.html"),),
        sources=(
            SourceRow(
                "MEDLINEPLUS", "MedlinePlus Connect", "NLM", "https://medlineplus.gov/", "MP"
            ),
        ),
        rule_sources=(RuleSource("REGLA-QUE-NO-EXISTE", "MEDLINEPLUS"),),
    )

    with pytest.raises(ImportFailed):
        apply_plan(migrated_engine, plan)

    assert _codigo_loinc(migrated_engine, GLU) is None  # the analyte write was rolled back
    assert (
        _count(migrated_engine, "SELECT COUNT(*) FROM conocimiento_contenidos") == before_contents
    )
    assert (
        _count(
            migrated_engine,
            "SELECT COUNT(*) FROM conocimiento_contenidos WHERE url_referencia = :u",
            u="https://medlineplus.gov/glu-test.html",
        )
        == 0
    )


def test_an_existing_terminology_code_is_never_overwritten(migrated_engine) -> None:
    plan = ImportPlan(analyte_codes=(AnalyteCode(HB, "99999-9"),))

    counts = apply_plan(migrated_engine, plan)

    assert _codigo_loinc(migrated_engine, HB) == "718-7"  # kept from the earlier import
    assert counts["loinc_codes_kept"] == 1

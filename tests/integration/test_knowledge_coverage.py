"""Does the knowledge base in the database answer the reviewed cases?
(knowledge/casos-de-prueba.csv)

Each case is a synthetic laboratory result with the answer the rules require.
- estado = verificado: the system must give the expected answer today.
- estado = brecha: the knowledge base does not hold the information needed, and the system
  answers wrongly today. The case is marked xfail(strict). When the base is improved and the case
  passes, the test turns into XPASS and fails, so the row must be updated to verificado.

The cases test the logic and the data available. They do not validate clinical content: that
requires a clinician reviewing the references.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from medicalbooking.modules.clinical.domain import PatientContext, classify
from medicalbooking.modules.clinical.infrastructure.repositories import SqlClinicalStore

pytestmark = pytest.mark.integration

CASES_FILE = Path(__file__).resolve().parents[2] / "knowledge" / "casos-de-prueba.csv"


def _cases() -> list[dict[str, str]]:
    with CASES_FILE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _param(row: dict[str, str]):
    if row["estado"] == "brecha":
        return pytest.param(
            row,
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"knowledge gap: {row['descripcion']}",
            ),
            id=row["caso_id"],
        )
    return pytest.param(row, id=row["caso_id"])


def _answer(engine, row: dict[str, str]) -> str:
    references = SqlClinicalStore(engine).list_validated_references(row["analito_id"], date.today())
    context = PatientContext(
        sexo=row["sexo"] or None,
        edad_dias=int(row["edad_anios"]) * 365 if row["edad_anios"] else None,
        metodo=row["metodo"] or None,
        tipo_muestra=row["tipo_muestra"] or None,
        contexto=row["contexto"] or None,
    )
    value = Decimal(row["valor"]) if row["valor"] else None
    return classify(value, row["unidad"] or None, references, context, date.today()).interpretacion


@pytest.mark.parametrize("row", [_param(row) for row in _cases()])
def test_the_knowledge_base_answers_the_case_as_the_rules_require(migrated_engine, row) -> None:
    assert _answer(migrated_engine, row) == row["resultado_esperado"]


def test_the_case_file_has_both_verified_and_gap_cases() -> None:
    states = {row["estado"] for row in _cases()}

    assert states == {"verificado", "brecha"}


def test_no_case_claims_a_category_the_base_cannot_support(migrated_engine) -> None:
    """Safety, independent of coverage: an analyte with no reference never gets a category."""
    unsupported = [row for row in _cases() if row["analito_id"].endswith("c001")]

    assert unsupported, "the case file must keep at least one unsupported analyte"
    for row in unsupported:
        assert _answer(migrated_engine, row) == "NO_EVALUABLE"

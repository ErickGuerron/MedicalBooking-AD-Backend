"""Can the WHO 2020 ferritin cut-offs (Table 1) live in the knowledge base? Against real SQL Server.

Loads the Table 1 rows as drafts through the knowledge API, shows they are not used until a doctor
validates them, validates them, then checks that the stored references classify ferritin exactly
like the domain rule (domain/iron.py) over a grid of patients and values.

Encoding limit: a reference has one context field. Pregnancy (first trimester) and inflammation
(CRP > 5 mg/L) both need it, so a pregnant patient in the first trimester with inflammation is
not represented. The test pins that gap instead of hiding it.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.clinical.domain import PatientContext, classify
from medicalbooking.modules.clinical.domain.iron import (
    DEFICIENCIA_HIERRO,
    DEFICIENCIA_HIERRO_CON_INFLAMACION,
    NO_EVALUABLE,
    SIN_DEFICIENCIA,
    SIN_DEFICIENCIA_CON_INFLAMACION,
    iron_finding,
)
from medicalbooking.modules.clinical.domain.references import BAJO, DENTRO
from medicalbooking.modules.clinical.infrastructure.repositories import SqlClinicalStore
from tests.integration.test_lab_api import _doctor
from tests.integration.test_scheduling_api import _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
SOURCE = "f0000000-0000-4000-8000-000000000001"  # synthetic source from seed 0006
UNIT = "ug/L"
DAYS_PER_YEAR = 365.25


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture(scope="module")
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _days(years: float) -> int:
    return int(years * DAYS_PER_YEAR)


# Table 1 rows, encoded as references. contexto carries the inflammation or pregnancy state.
TABLE_1_ROWS = [
    {
        "name": "0-59 meses sin inflamacion",
        "minimo": 12,
        "edad_min": None,
        "edad_max": _days(5) - 1,
        "contexto": "SIN_INFLAMACION",
    },
    {
        "name": "5 anios o mas sin inflamacion",
        "minimo": 15,
        "edad_min": _days(5),
        "edad_max": None,
        "contexto": "SIN_INFLAMACION",
    },
    {
        "name": "0-59 meses con inflamacion",
        "minimo": 30,
        "edad_min": None,
        "edad_max": _days(5) - 1,
        "contexto": "INFLAMACION",
    },
    {
        "name": "5 anios o mas con inflamacion",
        "minimo": 70,
        "edad_min": _days(5),
        "edad_max": None,
        "contexto": "INFLAMACION",
    },
    {
        "name": "embarazo primer trimestre",
        "minimo": 15,
        "edad_min": _days(10),
        "edad_max": _days(60) - 1,
        "contexto": "EMBARAZO-T1",
    },
]


@pytest.fixture(scope="module")
def ferritin_analyte(app: Flask, engine: Engine) -> str:
    admin = _token(app, _user(engine, "ADMINISTRADOR"))
    response = app.test_client().post(
        "/api/v1/knowledge/analytes",
        json={"nombre": f"Ferritina (WHO 2020) {uuid.uuid4()}", "unidad": UNIT},
        headers=admin,
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _as_draft(app: Flask, engine: Engine, analito: str, row: dict) -> str:
    body = {
        "fuente_id": SOURCE,
        "unidad": UNIT,
        "minimo": row["minimo"],
        "maximo": None,
        "edad_min_dias": row["edad_min"],
        "edad_max_dias": row["edad_max"],
        "contexto": row["contexto"],
        "fecha_inicio": "2020-01-01",
    }
    response = app.test_client().post(
        f"/api/v1/knowledge/analytes/{analito}/references",
        json=body,
        headers=_token(app, _user(engine, "ADMINISTRADOR")),
    )
    assert response.status_code == 201, (row["name"], response.get_json())
    return response.get_json()["id"]


def _validate(app: Flask, engine: Engine, reference_id: str) -> int:
    return (
        app.test_client()
        .post(
            f"/api/v1/knowledge/references/{reference_id}/validate",
            headers=_token(app, _doctor(engine)),
        )
        .status_code
    )


def _kb_context(sex: str, years: int, crp: Decimal, pregnancy: str | None) -> str | None:
    """Map a patient to the single contexto field the references use."""
    base = "INFLAMACION" if crp > 5 else "SIN_INFLAMACION"
    if sex == "F" and 10 <= years < 60:
        if pregnancy is None:
            return None  # unknown pregnancy: never matches a context-bound reference
        if pregnancy == "NO":
            return base
        if pregnancy == "T1":
            return "EMBARAZO-T1"  # first trimester wins over inflammation: the encoding gap
        return "EMBARAZO-T2"  # no reference for later trimesters
    if pregnancy in ("T1", "T2", "T3"):
        return "NO_APLICA"  # a trimester outside women aged 10 to 59 matches nothing
    return base


DOMAIN_TO_KB = {
    DEFICIENCIA_HIERRO: BAJO,
    DEFICIENCIA_HIERRO_CON_INFLAMACION: BAJO,
    SIN_DEFICIENCIA: DENTRO,
    SIN_DEFICIENCIA_CON_INFLAMACION: DENTRO,
    NO_EVALUABLE: NO_EVALUABLE,
}

FERRITIN_VALUES = [
    Decimal(v) for v in ("5", "11.9", "12", "14.9", "15", "29.9", "30", "69.9", "70")
]
CRP_VALUES = [Decimal(v) for v in ("1.0", "5.0", "5.1", "9.0")]
AGES_YEARS = [0, 1, 4, 5, 9, 10, 15, 25, 59, 60, 70]
PATIENTS = [("F", p) for p in (None, "NO", "T1", "T2")] + [("M", p) for p in (None, "T1")]


@pytest.fixture(scope="module")
def ferritin_drafts(app: Flask, engine: Engine, ferritin_analyte: str) -> list[str]:
    return [_as_draft(app, engine, ferritin_analyte, row) for row in TABLE_1_ROWS]


def test_drafts_are_accepted_and_not_used_until_a_doctor_validates(
    app, engine, ferritin_analyte, ferritin_drafts
):
    store = SqlClinicalStore(engine)

    before = store.list_validated_references(ferritin_analyte, date.today())
    verdict = classify(
        Decimal("10"),
        UNIT,
        before,
        PatientContext("F", _days(30), None, None, contexto="SIN_INFLAMACION"),
        date.today(),
    )

    assert len(ferritin_drafts) == len(TABLE_1_ROWS)
    assert before == []
    assert verdict.interpretacion == NO_EVALUABLE


def test_validated_rows_are_accepted_without_overlap_conflicts(app, engine, ferritin_drafts):
    statuses = [_validate(app, engine, draft) for draft in ferritin_drafts]

    assert statuses == [200] * len(ferritin_drafts), statuses


def test_stored_references_match_the_domain_rule_on_a_grid(app, engine, ferritin_analyte):
    store = SqlClinicalStore(engine)
    references = store.list_validated_references(ferritin_analyte, date.today())
    assert len(references) == len(TABLE_1_ROWS)

    mismatches = []
    checked = 0
    for (sex, pregnancy), years, value, crp in itertools.product(
        PATIENTS, AGES_YEARS, FERRITIN_VALUES, CRP_VALUES
    ):
        domain = iron_finding(value, crp, sex, years * 12, pregnancy).categoria
        kb_context = _kb_context(sex, years, crp, pregnancy)
        patient = PatientContext(sex, _days(years), None, None, contexto=kb_context)
        kb = classify(value, UNIT, references, patient, date.today()).interpretacion
        checked += 1
        if DOMAIN_TO_KB[domain] != kb:
            mismatches.append((sex, pregnancy, years, str(value), str(crp), domain, kb))

    gap = [
        m
        for m in mismatches
        if m[0] == "F" and m[1] == "T1" and 10 <= m[2] < 60 and m[4] in ("5.1", "9.0")
    ]
    assert checked == len(PATIENTS) * len(AGES_YEARS) * len(FERRITIN_VALUES) * len(CRP_VALUES)
    assert mismatches == gap, mismatches[:5]
    assert gap, "the pregnancy-plus-inflammation gap must be exercised"

"""The laboratory catalog of the demo seed is internally consistent before it reaches a database."""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "seed_demo", Path(__file__).parents[2] / "scripts" / "seed_demo.py"
)
seed = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = seed
_SPEC.loader.exec_module(seed)

SPECIALTY_NAMES = {name for name, _ in seed.SPECIALTIES}


def test_every_service_points_to_a_declared_specialty_or_none() -> None:
    for name, _minutes, _price, specialty, _order in seed.SERVICE_CATALOG:
        assert specialty is None or specialty in SPECIALTY_NAMES, name


def test_every_doctor_of_the_plan_has_a_declared_specialty() -> None:
    assert set(seed.DOCTOR_SPECIALTY.values()) <= SPECIALTY_NAMES
    assert set(seed.DOCTOR_SPECIALTY) == {f"medico{n}" for n in range(1, seed.DOCTOR_COUNT + 1)}


def test_the_booking_service_is_a_medical_consultation_in_the_catalog() -> None:
    names = [name for name, *_ in seed.SERVICE_CATALOG]
    assert seed.BOOKING_SERVICE in names
    booking = next(row for row in seed.SERVICE_CATALOG if row[0] == seed.BOOKING_SERVICE)
    assert booking[3] is not None


@pytest.mark.parametrize("name", [row[0] for row in seed.SERVICE_CATALOG])
def test_service_names_are_unique_and_prices_are_money(name: str) -> None:
    assert [row[0] for row in seed.SERVICE_CATALOG].count(name) == 1
    price = next(row[2] for row in seed.SERVICE_CATALOG if row[0] == name)
    assert Decimal(price) >= 0 and price == f"{Decimal(price):.2f}"


def test_the_lab_exams_are_priced_at_fifteen_and_last_forty_five_minutes() -> None:
    exams = {"Hemograma completo", "Perfil lipídico", "Glucosa en ayunas"}
    rows = {row[0]: row for row in seed.SERVICE_CATALOG if row[0] in exams}
    assert set(rows) == exams
    assert all(row[1] == 45 and row[2] == "15.00" for row in rows.values())

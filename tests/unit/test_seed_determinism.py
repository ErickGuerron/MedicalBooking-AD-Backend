"""Demo seed determinism: derived ids, seeded profile values and random passwords.

Pure functions only: no database and no network. The seed script is loaded from its path, the same
way the other seed tests load it.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from faker import Faker

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"


@pytest.fixture(scope="module")
def seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _faker(seed_value: int) -> Faker:
    fake = Faker("es_MX")
    fake.seed_instance(seed_value)
    return fake


def test_demo_id_is_a_stable_uuid_for_a_key(seed) -> None:
    first = seed.demo_id("agenda:medico1")

    assert first == seed.demo_id("agenda:medico1")
    assert str(uuid.UUID(first)) == first


def test_demo_id_differs_between_keys(seed) -> None:
    assert seed.demo_id("agenda:medico1") != seed.demo_id("agenda:medico2")


def test_a_known_valid_cedula_passes_the_check_digit(seed) -> None:
    assert seed.cedula_is_valid("1710034065")


def test_a_cedula_with_a_wrong_check_digit_is_rejected(seed) -> None:
    assert not seed.cedula_is_valid("1710034066")


def test_generated_cedulas_are_valid_ten_digit_and_unique(seed) -> None:
    fake = _faker(20261113)
    taken: set[str] = set()

    values = [seed.new_cedula(fake.random, taken) for _ in range(200)]

    assert len(set(values)) == 200
    for value in values:
        assert len(value) == 10 and value.isdigit()
        assert seed.cedula_is_valid(value)
        assert 1 <= int(value[:2]) <= 24
        assert int(value[2]) < 6


def test_a_cedula_already_taken_is_skipped(seed) -> None:
    first = seed.new_cedula(_faker(7).random, set())
    taken = {first}

    again = seed.new_cedula(_faker(7).random, taken)

    assert again != first
    assert again in taken


def test_demo_phone_is_a_ten_digit_mobile_number(seed) -> None:
    phone = seed.demo_phone(_faker(3).random)

    assert len(phone) == 10
    assert phone.isdigit()
    assert phone.startswith("09")


def test_profile_is_the_same_for_the_same_seed(seed) -> None:
    def run(seed_value: int) -> list:
        fake = _faker(seed_value)
        taken: set[str] = set()
        return [seed.patient_profile(fake, sex, taken) for sex in ("M", "F", "M")]

    first_run = run(20261113)
    second_run = run(20261113)
    other_seed_run = run(1)

    assert first_run == second_run
    assert first_run != other_seed_run


def test_profile_values_follow_the_clinical_rules(seed) -> None:
    fake = _faker(20261113)
    taken: set[str] = set()
    today = date.today()

    for sex in ("M", "F") * 20:
        profile = seed.patient_profile(fake, sex, taken)

        assert profile.tipo_documento == "CEDULA"
        assert seed.cedula_is_valid(profile.numero_documento)
        assert profile.sexo_registrado == sex
        assert profile.fecha_nacimiento <= today
        assert 18 <= (today - profile.fecha_nacimiento).days / 365.25 <= 120
        assert len(profile.telefono_emergencia) <= 25
        assert len(profile.contacto_emergencia) <= 150


def test_passwords_stay_random_on_every_call(seed) -> None:
    first, second = seed.new_password(), seed.new_password()

    assert first != second
    assert len(first) >= 12

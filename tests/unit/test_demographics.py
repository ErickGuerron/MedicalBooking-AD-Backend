"""Sex and birth date rules, and the patient profile use case with an in-memory store."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from medicalbooking.modules.clinical.application.clinical_profile import (
    PERM_EDIT,
    ClinicalProfile,
    PatientProfileService,
)
from medicalbooking.modules.clinical.application.ports import UNSET
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.demographics import parse_birth_date, validate_sex
from medicalbooking.shared.domain.errors import Forbidden, ValidationFailed

TODAY = date(2026, 6, 15)
NOW = datetime(2026, 6, 15, 12, 0)


@pytest.mark.parametrize("value", ["M", "F"])
def test_sex_accepts_only_m_and_f(value: str) -> None:
    assert validate_sex(value) == value


@pytest.mark.parametrize("value", ["m", "X", "", "MALE", " M"])
def test_sex_rejects_everything_else(value: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_sex(value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1990-01-31", date(1990, 1, 31)),
        ("2026-06-15", date(2026, 6, 15)),  # born today
        ("1906-06-15", date(1906, 6, 15)),  # exactly 120 today
        ("1905-06-16", date(1905, 6, 16)),  # still 120 until tomorrow
    ],
)
def test_birth_date_accepts_a_plausible_past_date(text: str, expected: date) -> None:
    assert parse_birth_date(text, TODAY) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026-06-16",  # future
        "1905-06-15",  # 121 today
        "1900-01-01",
        "2026-02-30",  # not a calendar date
        "1990-1-5",
        "19900105",
        "1990-01-05T00:00:00",
        "٢٠٠٠-٠١-٠١",  # non-ASCII digits
        "",
    ],
)
def test_birth_date_rejects_future_ancient_and_malformed(text: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_birth_date(text, TODAY)


class FakeStore:
    def __init__(self) -> None:
        self.row: list[Any] = [None, None, None, "F", date(1990, 1, 1)]
        self.audits: list[Any] = []

    def save_patient_clinical_profile(
        self, usuario_id, altitud, fumador, cigarrillos, sexo=UNSET, fecha_nacimiento=UNSET
    ):
        self.row[:3] = [altitud, fumador, cigarrillos]
        if sexo is not UNSET:
            self.row[3] = sexo
        if fecha_nacimiento is not UNSET:
            self.row[4] = fecha_nacimiento
        return "patient-1"

    def own_clinical_profile(self, usuario_id):
        return tuple(self.row)

    def record_audit(self, event) -> None:
        self.audits.append(event)


def _service() -> tuple[PatientProfileService, FakeStore]:
    store = FakeStore()
    return PatientProfileService(store, lambda: NOW), store  # type: ignore[arg-type]


def _actor(permissions: frozenset[str] = frozenset({PERM_EDIT})) -> AccessProfile:
    return AccessProfile("u1", "ACTIVO", frozenset({"PACIENTE"}), permissions)


BASE = ClinicalProfile(altitud_residencia_m=100, fumador=False, cigarrillos_dia=None)


def test_omitted_demographics_stay_as_stored() -> None:
    service, store = _service()

    saved = service.save(_actor(), BASE)

    assert (saved.sexo, saved.fecha_nacimiento) == ("F", date(1990, 1, 1))
    assert store.row[3:] == ["F", date(1990, 1, 1)]


def test_null_clears_and_a_value_replaces() -> None:
    service, store = _service()

    cleared = service.save(_actor(), BASE, sexo=None, fecha_nacimiento=None)
    replaced = service.save(_actor(), BASE, sexo="M", fecha_nacimiento="1980-02-29")

    assert (cleared.sexo, cleared.fecha_nacimiento) == (None, None)
    assert (replaced.sexo, replaced.fecha_nacimiento) == ("M", date(1980, 2, 29))
    assert len(store.audits) == 2


@pytest.mark.parametrize(
    ("sexo", "born"), [("X", UNSET), (UNSET, "2999-01-01"), (UNSET, "1800-01-01")]
)
def test_invalid_demographics_store_nothing(sexo, born) -> None:
    service, store = _service()

    with pytest.raises(ValidationFailed):
        service.save(_actor(), BASE, sexo=sexo, fecha_nacimiento=born)
    assert store.row == [None, None, None, "F", date(1990, 1, 1)]
    assert store.audits == []


def test_a_user_without_the_permission_cannot_write_demographics() -> None:
    service, store = _service()

    with pytest.raises(Forbidden):
        service.save(_actor(frozenset()), BASE, sexo="M")
    assert store.row[3] == "F"
    assert store.audits[0].resultado == "DENEGADO"

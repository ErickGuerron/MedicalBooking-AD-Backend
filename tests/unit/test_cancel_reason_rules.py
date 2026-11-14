"""Reason rules for cancelling an appointment (doctor cancellation, scheduling).

A doctor must give a closed reason code and a text of 1 to 1500 characters. A patient's
cancellation ignores the reason fields. Checked through the use case with an in-memory store
that honours the same contract as the SQL adapter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    Appointment,
    CancelCommand,
    CancelOutcome,
    CancelStatus,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.shared.domain.errors import Forbidden, ValidationFailed

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PERM_CANCEL_MEDICO = "citas.cancelar_medico"
PERM_BOOK = "citas.reservar"
REASON_REQUIRED = "MOTIVO_CANCELACION_REQUERIDO"
REASON_INVALID = "MOTIVO_CANCELACION_INVALIDO"


def _id() -> str:
    return str(uuid.uuid4())


def _profile(*, permissions: frozenset[str], user_id: str | None = None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset(),
        permissions=permissions,
    )


@dataclass
class CancelStore:
    """Records every cancel command. Ownership follows the adapter's rule for each mode."""

    appointments: dict[str, Appointment] = field(default_factory=dict)
    agenda_doctor: dict[str, str] = field(default_factory=dict)
    commands: list[CancelCommand] = field(default_factory=list)

    def cancel(self, command: CancelCommand) -> CancelOutcome:
        self.commands.append(command)
        appointment = self.appointments.get(command.appointment_id)
        if appointment is None:
            return CancelOutcome(CancelStatus.NOT_FOUND_OR_FORBIDDEN, None)
        if command.as_medico:
            owned = self.agenda_doctor.get(command.appointment_id) == command.actor_user_id
        else:
            owned = appointment.patient_user_id == command.actor_user_id
        if not owned:
            return CancelOutcome(CancelStatus.NOT_FOUND_OR_FORBIDDEN, None)
        cancelled = replace(appointment, estado="CANCELADA")
        self.appointments[cancelled.id] = cancelled
        return CancelOutcome(CancelStatus.CANCELLED, cancelled)


@pytest.fixture
def patient() -> AccessProfile:
    return _profile(permissions=frozenset({PERM_BOOK}))


@pytest.fixture
def doctor() -> AccessProfile:
    return _profile(permissions=frozenset({PERM_CANCEL_MEDICO}))


@pytest.fixture
def store(patient: AccessProfile, doctor: AccessProfile) -> CancelStore:
    booked = Appointment(
        id=_id(),
        key="cancel-rules-key-0001",
        patient_user_id=patient.user_id,
        horario_id=_id(),
        servicio_id=_id(),
        estado="PENDIENTE_PAGO",
        motivo=None,
        expiracion_reserva=NOW + timedelta(minutes=15),
    )
    return CancelStore(
        appointments={booked.id: booked},
        agenda_doctor={booked.id: doctor.user_id},
    )


def _appointment_id(store: CancelStore) -> str:
    return next(iter(store.appointments))


def _service(store: CancelStore) -> SchedulingService:
    return SchedulingService(store=store, clock=lambda: NOW, hold_minutes=15)


# ---------- the closed reason set ----------


@pytest.mark.parametrize("codigo", ["IMPREVISTO_MEDICO", "OTRO_MEDICO"])
def test_doctor_reason_codes_in_the_closed_set_are_accepted(store, doctor, codigo) -> None:
    cancelled = _service(store).cancel(
        doctor,
        _appointment_id(store),
        motivo_codigo=codigo,
        motivo_texto="Motivo de prueba",
    )

    assert cancelled.estado == "CANCELADA"
    assert store.commands[-1].motivo_codigo == codigo


@pytest.mark.parametrize(
    "codigo",
    [
        "CANCELACION_PACIENTE",
        "CANCELACION_MEDICO",
        "ERROR_DE_COBRO",
        "imprevisto_medico",
        " IMPREVISTO_MEDICO",
        "",
    ],
)
def test_reason_codes_outside_the_closed_set_are_rejected(store, doctor, codigo) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _service(store).cancel(
            doctor, _appointment_id(store), motivo_codigo=codigo, motivo_texto="Texto"
        )

    assert refused.value.detail == REASON_INVALID
    assert store.commands == [], "a rejected reason must never reach the store"


def test_a_missing_reason_code_is_required(store, doctor) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _service(store).cancel(doctor, _appointment_id(store), motivo_texto="Texto")

    assert refused.value.detail == REASON_REQUIRED
    assert store.commands == []


# ---------- the reason text ----------


@pytest.mark.parametrize("texto", ["x", "x" * 1500, "Imprevisto en quirofano"])
def test_reason_text_from_one_to_1500_characters_is_accepted(store, doctor, texto) -> None:
    cancelled = _service(store).cancel(
        doctor, _appointment_id(store), motivo_codigo="OTRO_MEDICO", motivo_texto=texto
    )

    assert cancelled.estado == "CANCELADA"
    assert store.commands[-1].motivo_texto == texto


@pytest.mark.parametrize("texto", [None, "", "   "])
def test_a_missing_or_blank_reason_text_is_required(store, doctor, texto) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _service(store).cancel(
            doctor, _appointment_id(store), motivo_codigo="OTRO_MEDICO", motivo_texto=texto
        )

    assert refused.value.detail == REASON_REQUIRED
    assert store.commands == []


def test_a_reason_text_over_1500_characters_is_invalid(store, doctor) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _service(store).cancel(
            doctor,
            _appointment_id(store),
            motivo_codigo="OTRO_MEDICO",
            motivo_texto="x" * 1501,
        )

    assert refused.value.detail == REASON_INVALID
    assert store.commands == []


# ---------- who may use the doctor path ----------


def test_a_user_with_neither_permission_is_forbidden(store) -> None:
    outsider = _profile(permissions=frozenset())

    with pytest.raises(Forbidden):
        _service(store).cancel(
            outsider,
            _appointment_id(store),
            motivo_codigo="OTRO_MEDICO",
            motivo_texto="Texto",
        )

    assert store.commands == []


# ---------- patient cancellations ignore the reason fields ----------


def test_a_patient_cancellation_needs_no_reason(store, patient) -> None:
    cancelled = _service(store).cancel(patient, _appointment_id(store))

    assert cancelled.estado == "CANCELADA"
    command = store.commands[-1]
    assert command.as_medico is False
    assert command.motivo_codigo is None
    assert command.motivo_texto is None


def test_a_patient_reason_fields_are_ignored_even_when_invalid(store, patient) -> None:
    cancelled = _service(store).cancel(
        patient,
        _appointment_id(store),
        motivo_codigo="BASURA",
        motivo_texto="x" * 900,
    )

    assert cancelled.estado == "CANCELADA"
    command = store.commands[-1]
    assert command.motivo_codigo is None
    assert command.motivo_texto is None


def test_a_doctor_command_carries_the_reason_to_the_store(store, doctor) -> None:
    _service(store).cancel(
        doctor,
        _appointment_id(store),
        motivo_codigo="IMPREVISTO_MEDICO",
        motivo_texto="Urgencia en el hospital",
    )

    command = store.commands[-1]
    assert command.as_medico is True
    assert command.actor_user_id == doctor.user_id
    assert command.motivo_codigo == "IMPREVISTO_MEDICO"
    assert command.motivo_texto == "Urgencia en el hospital"

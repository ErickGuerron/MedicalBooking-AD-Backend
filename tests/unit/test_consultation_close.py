"""POST /appointments/{id}/consultation/close: clinical note rules and the one-way close."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    CloseConsultationCommand,
    Consultation,
    ConsultationOutcome,
    ConsultationStatus,
)
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.domain.policies import normalize_clinical_note
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed
from tests.unit.test_scheduling_service import FakeStore

NOW = datetime(2026, 11, 16, 13, 0, tzinfo=UTC)
CITA = "44444444-4444-4444-8444-444444444444"
DOCTOR = AccessProfile("d", "ACTIVO", frozenset({"MEDICO"}), frozenset({"consultas.abrir"}))
PATIENT = AccessProfile("p", "ACTIVO", frozenset({"PACIENTE"}), frozenset({"citas.reservar"}))


@dataclass
class CloseStore(FakeStore):
    status: ConsultationStatus = ConsultationStatus.CLOSED
    commands: list[CloseConsultationCommand] = field(default_factory=list)

    def close_consultation(self, command: CloseConsultationCommand) -> ConsultationOutcome:
        self.commands.append(command)
        if self.status is not ConsultationStatus.CLOSED:
            return ConsultationOutcome(self.status, None)
        consultation = Consultation(
            "c1",
            command.appointment_id,
            "CERRADA",
            NOW,
            None,
            command.observaciones,
            command.plan_tratamiento,
            NOW,
        )
        return ConsultationOutcome(self.status, consultation)


def _service(store: CloseStore) -> SchedulingService:
    return SchedulingService(store=store, clock=lambda: NOW, hold_minutes=15)


def test_clinical_note_is_optional_trimmed_and_limited() -> None:
    assert normalize_clinical_note("observaciones", None) is None
    assert normalize_clinical_note("observaciones", "   ") is None
    assert normalize_clinical_note("observaciones", "  Faringitis aguda  ") == "Faringitis aguda"
    assert normalize_clinical_note("observaciones", "Amoxicilina 500mg c/8h (7 días), 50%.")
    with pytest.raises(ValidationFailed):
        normalize_clinical_note("observaciones", "a" * 1001)
    with pytest.raises(ValidationFailed):
        normalize_clinical_note("observaciones", 42)


@pytest.mark.parametrize("text", ["Gripe 🤒", "Diagnóstico <script>", "Dolor © 2026", "a | b"])
def test_clinical_note_refuses_emojis_and_symbols(text: str) -> None:
    with pytest.raises(ValidationFailed):
        normalize_clinical_note("observaciones", text)


def test_close_passes_the_clean_notes_and_the_allowed_states_to_the_store() -> None:
    store = CloseStore()
    result = _service(store).close_consultation(
        DOCTOR,
        CITA,
        observaciones=" Hipertensión grado 1 ",
        plan_tratamiento=None,
    )
    command = store.commands[0]
    assert command.observaciones == "Hipertensión grado 1"
    assert command.plan_tratamiento is None
    assert command.allowed_states == frozenset({"CONFIRMADA", "COMPLETADA"})
    assert result.estado == "CERRADA"
    assert result.observaciones == "Hipertensión grado 1"


@pytest.mark.parametrize("diagnosis", [None, "", "   "])
def test_close_requires_the_diagnosis(diagnosis: str | None) -> None:
    store = CloseStore()
    with pytest.raises(ValidationFailed):
        _service(store).close_consultation(
            DOCTOR, CITA, observaciones=diagnosis, plan_tratamiento="Reposo"
        )
    assert store.commands == []


def test_close_requires_the_doctor_permission() -> None:
    with pytest.raises(Forbidden):
        _service(CloseStore()).close_consultation(
            PATIENT, CITA, observaciones="Dolor torácico", plan_tratamiento=None
        )


def test_missing_or_foreign_appointment_is_forbidden() -> None:
    store = CloseStore(status=ConsultationStatus.NOT_FOUND_OR_FORBIDDEN)
    with pytest.raises(Forbidden):
        _service(store).close_consultation(
            DOCTOR, CITA, observaciones="Dolor torácico", plan_tratamiento=None
        )


def test_wrong_state_and_repeat_close_are_conflicts() -> None:
    with pytest.raises(Conflict):
        _service(CloseStore(status=ConsultationStatus.STATE_NOT_ALLOWED)).close_consultation(
            DOCTOR, CITA, observaciones="Dolor torácico", plan_tratamiento=None
        )
    with pytest.raises(Conflict):
        _service(CloseStore(status=ConsultationStatus.ALREADY_CLOSED)).close_consultation(
            DOCTOR, CITA, observaciones="Dolor torácico", plan_tratamiento=None
        )

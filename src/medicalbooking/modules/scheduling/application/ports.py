"""Ports for scheduling. The SQL adapter implements them; use cases never import SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


@dataclass(frozen=True)
class AvailableSlot:
    horario_id: str
    medico_id: str
    inicio_utc: datetime
    fin_utc: datetime


@dataclass(frozen=True)
class Appointment:
    id: str
    key: str
    patient_user_id: str
    horario_id: str
    servicio_id: str
    estado: str
    motivo: str | None
    expiracion_reserva: datetime | None


@dataclass(frozen=True)
class ReserveCommand:
    key: str
    patient_user_id: str
    horario_id: str
    servicio_id: str
    motivo: str | None
    now: datetime
    hold_until: datetime


class ReserveStatus(Enum):
    CREATED = "created"
    SLOT_UNAVAILABLE = "slot_unavailable"
    NO_PATIENT_PROFILE = "no_patient_profile"


@dataclass(frozen=True)
class ReserveOutcome:
    status: ReserveStatus
    appointment: Appointment | None


@dataclass(frozen=True)
class CancelCommand:
    actor_user_id: str
    appointment_id: str
    now: datetime


class CancelStatus(Enum):
    CANCELLED = "cancelled"
    NOT_FOUND_OR_FORBIDDEN = "not_found_or_forbidden"
    NOT_CANCELLABLE = "not_cancellable"


@dataclass(frozen=True)
class CancelOutcome:
    status: CancelStatus
    appointment: Appointment | None


class SchedulingStore(Protocol):
    def list_available(
        self, start: datetime, end: datetime, medico_id: str | None, limit: int
    ) -> list[AvailableSlot]: ...

    def find_slot(self, horario_id: str) -> datetime | None:
        """Return the slot start (timezone-aware UTC), or None when it does not exist."""
        ...

    def service_is_active(self, servicio_id: str) -> bool: ...

    def find_appointment_by_key(self, key: str) -> Appointment | None: ...

    def reserve(self, command: ReserveCommand) -> ReserveOutcome: ...

    def cancel(self, command: CancelCommand) -> CancelOutcome: ...

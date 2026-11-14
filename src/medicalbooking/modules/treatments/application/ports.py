"""Ports for treatments. Adapters implement them; use cases never import SQL.

Audit events reuse the laboratory audit record, so every module writes the same auditoria_accesos
row shape. Clinical text never goes into an AuditEvent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from medicalbooking.modules.laboratory.application.ports import AuditEvent


@dataclass(frozen=True)
class CitaContext:
    """The owners of an appointment, resolved from citas -> horarios -> agendas -> medicos."""

    cita_id: str
    paciente_id: str
    medico_id: str


@dataclass(frozen=True)
class Treatment:
    id: str
    cita_id: str
    paciente_id: str
    paciente_user_id: str
    medico_id: str
    medico_user_id: str
    descripcion: str
    estado: str
    fecha_inicio: date
    fecha_fin: date | None


@dataclass(frozen=True)
class FollowUp:
    id: str
    tratamiento_id: str
    nota: str
    registrado_en: datetime


@dataclass(frozen=True)
class NewTreatment:
    treatment_id: str
    cita_id: str
    descripcion: str
    estado: str
    fecha_inicio: date
    fecha_fin: date | None
    creado_en: datetime


@dataclass(frozen=True)
class TreatmentUpdate:
    """Values after the service merged the patch. expected_estado guards the state change."""

    treatment_id: str
    expected_estado: str
    descripcion: str
    estado: str
    fecha_fin: date | None
    actualizado_en: datetime


@dataclass(frozen=True)
class NewFollowUp:
    followup_id: str
    tratamiento_id: str
    nota: str
    registrado_por_user_id: str
    registrado_en: datetime


class TreatmentStore(Protocol):
    def resolve_patient_id(self, user_id: str) -> str | None: ...

    def resolve_medico_id(self, user_id: str) -> str | None: ...

    def get_cita(self, cita_id: str) -> CitaContext | None: ...

    def create_treatment(self, command: NewTreatment) -> Treatment: ...

    def get_treatment(self, treatment_id: str) -> Treatment | None: ...

    def list_treatments(self, cita_id: str) -> list[Treatment]:
        """Newest fecha_inicio first, then id ascending."""
        ...

    def update_treatment(self, command: TreatmentUpdate) -> Treatment | None:
        """Update only while the stored estado still equals expected_estado; None otherwise."""
        ...

    def append_followup(self, command: NewFollowUp) -> FollowUp: ...

    def list_followups(self, tratamiento_id: str) -> list[FollowUp]:
        """In insertion order (the adapter's identity sequence), never by id.

        Two notes can share one registration instant, so the sequence is the only reliable
        order. Follow-ups are never updated or deleted.
        """
        ...

    def record_audit(self, event: AuditEvent) -> None: ...

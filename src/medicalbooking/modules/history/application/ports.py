"""Ports for the clinical history. The SQL adapter implements them; use cases never import SQL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from medicalbooking.modules.laboratory.application.ports import AuditEvent


@dataclass(frozen=True)
class Entry:
    """One history entry of any kind. `data` holds the kind's own fields."""

    id: str
    kind: str
    paciente_id: str
    estado: str | None
    origen: str
    verificada: bool
    verificada_por: str | None
    verificada_en: datetime | None
    registrada_por: str
    registrada_en: datetime
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VitalSigns:
    id: str
    paciente_id: str
    cita_id: str
    corrige_a: str | None
    motivo_correccion: str | None
    registrada_por: str
    registrada_en: datetime
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventRow:
    """One line of the append-only event log. `actor_usuario_id` is None when not visible."""

    id: int
    entidad: str
    entidad_id: str
    accion: str
    actor_usuario_id: str | None
    motivo: str | None
    antes: str | None
    despues: str | None
    ocurrido_en: datetime


@dataclass(frozen=True)
class CatalogItem:
    codigo: str
    nombre: str
    activo: bool
    categoria: str | None = None


@dataclass(frozen=True)
class CitaOwnership:
    """Who an appointment belongs to: the patient and the doctor of its agenda."""

    cita_id: str
    paciente_id: str
    medico_id: str


class HistoryStore(Protocol):
    def resolve_patient_id(self, user_id: str) -> str | None: ...

    def resolve_medico_id(self, user_id: str) -> str | None: ...

    def doctor_treats_patient(self, medico_id: str, paciente_id: str) -> bool:
        """True when the doctor has at least one appointment with the patient, in any state."""
        ...

    def get_cita(self, cita_id: str) -> CitaOwnership | None: ...

    def insert_entry(
        self,
        kind: str,
        entry_id: str,
        paciente_id: str,
        values: dict[str, Any],
        *,
        origen: str,
        verificada: bool,
        verificada_por: str | None,
        registrada_por: str,
        now: datetime,
    ) -> None: ...

    def get_entry(self, kind: str, entry_id: str) -> Entry | None: ...

    def list_entries(
        self,
        kind: str,
        paciente_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Entry]:
        """Entries registered in [start, end). Either bound may be None: that side is open."""
        ...

    def verify_entry(self, kind: str, entry_id: str, medico_user_id: str, now: datetime) -> bool:
        """Sets verified only if the entry is still unverified. False when it already was."""
        ...

    def change_estado(
        self, kind: str, entry_id: str, expected: str, target: str, now: datetime
    ) -> bool:
        """Changes the state only if it is still `expected`. False when someone else changed it."""
        ...

    def insert_vitals(
        self,
        vitals_id: str,
        paciente_id: str,
        cita_id: str,
        values: dict[str, Any],
        *,
        registrada_por: str,
        now: datetime,
        corrige_a: str | None = None,
        motivo_correccion: str | None = None,
    ) -> None: ...

    def get_vitals(self, vitals_id: str) -> VitalSigns | None: ...

    def list_vitals(
        self, paciente_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[VitalSigns]: ...

    def list_events(
        self,
        paciente_id: str,
        *,
        entidad: str | None,
        accion: str | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[EventRow], int]:
        """One page of the event log, newest first, and the total matching the filters."""
        ...

    def catalog_item(self, catalog: str, codigo: str) -> CatalogItem | None: ...

    def search_catalog(self, catalog: str, query: str | None, limit: int) -> list[CatalogItem]: ...

    def append_event(
        self,
        *,
        paciente_id: str,
        entidad: str,
        entidad_id: str,
        accion: str,
        actor_user_id: str,
        motivo: str | None,
        antes: str | None,
        despues: str | None,
        now: datetime,
    ) -> None:
        """Appends to historia_eventos. The table refuses updates and deletes."""
        ...

    def record_audit(self, event: AuditEvent) -> None: ...

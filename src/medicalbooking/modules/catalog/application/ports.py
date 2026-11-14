"""Ports for the catalog. The SQL adapter implements them; use cases never import SQL."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from medicalbooking.modules.catalog.domain.policies import AttentionSchedule


@dataclass(frozen=True)
class Specialty:
    id: int
    nombre: str
    descripcion: str | None


@dataclass(frozen=True)
class SpecialtyRef:
    id: int
    nombre: str


@dataclass(frozen=True)
class Doctor:
    """Public view of a doctor: no professional registry, no e-mail."""

    id: str
    nombre: str
    especialidades: tuple[SpecialtyRef, ...]
    duracion_cita_min: int
    horario_atencion: AttentionSchedule


@dataclass(frozen=True)
class Service:
    id: str
    nombre: str
    duracion_min: int
    precio_base: Decimal
    moneda: str
    especialidad_id: int | None


class WriteStatus(Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    UNKNOWN_SPECIALTY = "unknown_specialty"
    DUPLICATE_NAME = "duplicate_name"


class CatalogStore(Protocol):
    def list_specialties(self) -> list[Specialty]:
        """Active specialties ordered by name."""
        ...

    def list_doctors(self, especialidad_id: int | None) -> list[Doctor]:
        """Active doctors ordered by name; only active specialties are shown."""
        ...

    def list_services(self) -> list[Service]:
        """Active services ordered by name."""
        ...

    def create_specialty(
        self, nombre: str, descripcion: str | None
    ) -> tuple[WriteStatus, Specialty | None]: ...

    def update_specialty(
        self, especialidad_id: int, changes: dict[str, object]
    ) -> tuple[WriteStatus, Specialty | None]:
        """Apply ``nombre``, ``descripcion`` and/or ``activo`` (only the keys present)."""
        ...

    def set_doctor_specialties(
        self, medico_id: str, especialidad_ids: list[int]
    ) -> tuple[WriteStatus, list[SpecialtyRef]]:
        """Replace the doctor's specialties; every id must be an active specialty."""
        ...

    def create_service(
        self,
        nombre: str,
        duracion_min: int,
        precio_base: Decimal,
        moneda: str,
        especialidad_id: int | None,
    ) -> tuple[WriteStatus, Service | None]: ...

    def set_service_specialty(
        self, servicio_id: str, especialidad_id: int | None
    ) -> tuple[WriteStatus, Service | None]: ...

    def record_audit(
        self, actor_user_id: str, entidad: str, entidad_id: str | None, accion: str, detalle: str
    ) -> None: ...

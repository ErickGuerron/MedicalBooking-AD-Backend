"""Sedes and consultorios administration: create, edit, and deactivate only when nothing is booked.

Business rules live here, not in routes or SQL. The SQL adapter only stores and reads.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.ports import (
    ConsultorioOwned,
    OfficeStore,
    SedeDetail,
)
from medicalbooking.modules.scheduling.domain.policies import PERM_MANAGE_OFFICES
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

SEDE_NOMBRE_MAX = 150
SEDE_DIRECCION_MAX = 300
CONSULTORIO_NOMBRE_MAX = 80

SEDE_INACTIVA = "SEDE_INACTIVA"
CONSULTORIO_DUPLICADO = "CONSULTORIO_DUPLICADO"
CITAS_FUTURAS = "CITAS_FUTURAS"


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


def _nombre(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("'nombre' is required")
    nombre = value.strip()
    if len(nombre) > maximum:
        raise ValidationFailed(f"'nombre' must be at most {maximum} characters")
    return nombre


def _direccion(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationFailed("'direccion' must be a string or null")
    direccion = value.strip()
    if not direccion:
        return None
    if len(direccion) > SEDE_DIRECCION_MAX:
        raise ValidationFailed(f"'direccion' must be at most {SEDE_DIRECCION_MAX} characters")
    return direccion


def _activo(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValidationFailed("'activo' must be a boolean")
    return value


class OfficeAdminService:
    def __init__(self, store: OfficeStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def list_sedes(self, actor: AccessProfile) -> list[SedeDetail]:
        _require(actor, PERM_MANAGE_OFFICES)
        return self._store.list_sedes()

    def create_sede(self, actor: AccessProfile, nombre: Any, direccion: Any) -> SedeDetail:
        _require(actor, PERM_MANAGE_OFFICES)
        sede_id = str(uuid.uuid4())
        self._store.insert_sede(sede_id, _nombre(nombre, SEDE_NOMBRE_MAX), _direccion(direccion))
        return self._sede(sede_id)

    def update_sede(
        self, actor: AccessProfile, sede_id: str, changes: dict[str, Any]
    ) -> SedeDetail:
        _require(actor, PERM_MANAGE_OFFICES)
        current = self._sede(sede_id)
        nombre = (
            _nombre(changes["nombre"], SEDE_NOMBRE_MAX) if "nombre" in changes else current.nombre
        )
        direccion = (
            _direccion(changes["direccion"]) if "direccion" in changes else current.direccion
        )
        activo = _activo(changes["activo"]) if "activo" in changes else current.activo
        if current.activo and not activo:
            self._guard_deactivation([c.id for c in current.consultorios])
        self._store.update_sede(sede_id, nombre, direccion, activo)
        return self._sede(sede_id)

    def create_consultorio(self, actor: AccessProfile, sede_id: str, nombre: Any) -> SedeDetail:
        _require(actor, PERM_MANAGE_OFFICES)
        sede = self._sede(sede_id)
        if not sede.activo:
            raise ValidationFailed("The sede is inactive", code=SEDE_INACTIVA)
        consultorio_id = str(uuid.uuid4())
        if not self._store.insert_consultorio(
            consultorio_id, sede_id, _nombre(nombre, CONSULTORIO_NOMBRE_MAX)
        ):
            raise Conflict(
                "A consultorio with that name already exists in this sede",
                code=CONSULTORIO_DUPLICADO,
            )
        return self._sede(sede_id)

    def update_consultorio(
        self, actor: AccessProfile, consultorio_id: str, changes: dict[str, Any]
    ) -> ConsultorioOwned:
        _require(actor, PERM_MANAGE_OFFICES)
        current = self._consultorio(consultorio_id)
        nombre = (
            _nombre(changes["nombre"], CONSULTORIO_NOMBRE_MAX)
            if "nombre" in changes
            else current.nombre
        )
        activo = _activo(changes["activo"]) if "activo" in changes else current.activo
        if activo and not current.activo:
            if not self._sede(current.sede_id).activo:
                raise ValidationFailed("The sede is inactive", code=SEDE_INACTIVA)
        if current.activo and not activo:
            self._guard_deactivation([consultorio_id])
        if not self._store.update_consultorio(consultorio_id, nombre, activo):
            raise Conflict(
                "A consultorio with that name already exists in this sede",
                code=CONSULTORIO_DUPLICADO,
            )
        return self._consultorio(consultorio_id)

    # ---------- helpers ----------

    def _guard_deactivation(self, consultorio_ids: list[str]) -> None:
        if consultorio_ids and self._store.has_future_bookings(consultorio_ids, self._clock()):
            raise Conflict(
                "There are future appointments in these consultorios; they cannot be deactivated",
                code=CITAS_FUTURAS,
            )

    def _sede(self, sede_id: str) -> SedeDetail:
        found = self._store.get_sede(sede_id)
        if found is None:
            raise NotFound("Sede not found")
        return found

    def _consultorio(self, consultorio_id: str) -> ConsultorioOwned:
        found = self._store.get_consultorio(consultorio_id)
        if found is None:
            raise NotFound("Consultorio not found")
        return found

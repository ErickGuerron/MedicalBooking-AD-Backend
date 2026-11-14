"""Catalog use cases: read-only listings for any signed-in user, admin-only maintenance."""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.catalog.application.ports import (
    CatalogStore,
    Doctor,
    Service,
    Specialty,
    SpecialtyRef,
    WriteStatus,
)
from medicalbooking.modules.catalog.domain.policies import PERM_ADMIN
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed


class CatalogService:
    def __init__(self, store: CatalogStore) -> None:
        self._store = store

    # ---------- reads: any authenticated user ----------

    def list_specialties(self, actor: AccessProfile) -> list[Specialty]:
        return self._store.list_specialties()

    def list_doctors(self, actor: AccessProfile, especialidad_id: int | None) -> list[Doctor]:
        return self._store.list_doctors(especialidad_id)

    def list_services(self, actor: AccessProfile) -> list[Service]:
        return self._store.list_services()

    # ---------- writes: catalogo.administrar ----------

    def create_specialty(
        self, actor: AccessProfile, *, nombre: str, descripcion: str | None
    ) -> Specialty:
        _require_admin(actor)
        status, specialty = self._store.create_specialty(nombre, descripcion)
        _raise_for(status)
        assert specialty is not None
        self._audit(actor, "especialidad", None, "crear", f"id={specialty.id}")
        return specialty

    def update_specialty(
        self, actor: AccessProfile, especialidad_id: int, changes: dict[str, object]
    ) -> Specialty:
        _require_admin(actor)
        if not changes:
            raise ValidationFailed("At least one field is required")
        status, specialty = self._store.update_specialty(especialidad_id, changes)
        _raise_for(status)
        assert specialty is not None
        self._audit(actor, "especialidad", None, "actualizar", f"id={especialidad_id}")
        return specialty

    def set_doctor_specialties(
        self, actor: AccessProfile, medico_id: str, especialidad_ids: list[int]
    ) -> list[SpecialtyRef]:
        _require_admin(actor)
        status, refs = self._store.set_doctor_specialties(medico_id, sorted(set(especialidad_ids)))
        _raise_for(status)
        ids = ",".join(str(ref.id) for ref in refs)
        self._audit(actor, "medico_especialidades", medico_id, "asignar", f"ids={ids}")
        return refs

    def create_service(
        self,
        actor: AccessProfile,
        *,
        nombre: str,
        duracion_min: int,
        precio_base: Decimal,
        moneda: str,
        especialidad_id: int | None,
    ) -> Service:
        _require_admin(actor)
        status, service = self._store.create_service(
            nombre, duracion_min, precio_base, moneda, especialidad_id
        )
        _raise_for(status)
        assert service is not None
        detail = f"especialidad={especialidad_id}"
        self._audit(actor, "servicio_medico", service.id, "crear", detail)
        return service

    def set_service_specialty(
        self, actor: AccessProfile, servicio_id: str, especialidad_id: int | None
    ) -> Service:
        _require_admin(actor)
        status, service = self._store.set_service_specialty(servicio_id, especialidad_id)
        _raise_for(status)
        assert service is not None
        detail = f"especialidad={especialidad_id}"
        self._audit(actor, "servicio_medico", servicio_id, "asignar", detail)
        return service

    def _audit(
        self, actor: AccessProfile, entidad: str, entidad_id: str | None, accion: str, detalle: str
    ) -> None:
        self._store.record_audit(actor.user_id, entidad, entidad_id, accion, detalle)


def _require_admin(actor: AccessProfile) -> None:
    if PERM_ADMIN not in actor.permissions:
        raise Forbidden("Access to this resource is not allowed")


def _raise_for(status: WriteStatus) -> None:
    if status is WriteStatus.NOT_FOUND:
        raise NotFound("Resource not found")
    if status is WriteStatus.UNKNOWN_SPECIALTY:
        raise ValidationFailed("Unknown or inactive specialty")
    if status is WriteStatus.DUPLICATE_NAME:
        raise Conflict("A specialty with this name already exists")

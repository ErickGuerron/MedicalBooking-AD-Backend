"""Sedes and consultorios administration rules (unit, in-memory store)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.scheduling.application.office_admin import (
    CITAS_FUTURAS,
    CONSULTORIO_DUPLICADO,
    SEDE_INACTIVA,
    OfficeAdminService,
)
from medicalbooking.modules.scheduling.application.ports import (
    ConsultorioDetail,
    ConsultorioOwned,
    SedeDetail,
)
from medicalbooking.modules.scheduling.domain.policies import PERM_MANAGE_OFFICES
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


@dataclass
class FakeOfficeStore:
    sedes: dict[str, dict] = field(default_factory=dict)
    consultorios: dict[str, dict] = field(default_factory=dict)
    # consultorio ids that have an appointment in an active state with a future slot
    booked: set[str] = field(default_factory=set)

    def _sede_detail(self, sede_id: str) -> SedeDetail:
        row = self.sedes[sede_id]
        offices = tuple(
            ConsultorioDetail(id=c["id"], nombre=c["nombre"], activo=c["activo"])
            for c in self.consultorios.values()
            if c["sede_id"] == sede_id
        )
        return SedeDetail(
            id=sede_id,
            nombre=row["nombre"],
            direccion=row["direccion"],
            activo=row["activo"],
            consultorios=offices,
        )

    def list_sedes(self):
        return [self._sede_detail(sede_id) for sede_id in self.sedes]

    def get_sede(self, sede_id):
        return self._sede_detail(sede_id) if sede_id in self.sedes else None

    def insert_sede(self, sede_id, nombre, direccion):
        self.sedes[sede_id] = {"nombre": nombre, "direccion": direccion, "activo": True}

    def update_sede(self, sede_id, nombre, direccion, activo):
        self.sedes[sede_id] = {"nombre": nombre, "direccion": direccion, "activo": activo}

    def get_consultorio(self, consultorio_id):
        row = self.consultorios.get(consultorio_id)
        if row is None:
            return None
        return ConsultorioOwned(
            id=consultorio_id, sede_id=row["sede_id"], nombre=row["nombre"], activo=row["activo"]
        )

    def insert_consultorio(self, consultorio_id, sede_id, nombre):
        if any(
            c["sede_id"] == sede_id and c["nombre"] == nombre for c in self.consultorios.values()
        ):
            return False
        self.consultorios[consultorio_id] = {
            "id": consultorio_id,
            "sede_id": sede_id,
            "nombre": nombre,
            "activo": True,
        }
        return True

    def update_consultorio(self, consultorio_id, nombre, activo):
        row = self.consultorios[consultorio_id]
        if any(
            c["sede_id"] == row["sede_id"] and c["nombre"] == nombre and c["id"] != consultorio_id
            for c in self.consultorios.values()
        ):
            return False
        row["nombre"], row["activo"] = nombre, activo
        return True

    def has_future_bookings(self, consultorio_ids, now):
        assert now == NOW
        return any(c in self.booked for c in consultorio_ids)


def _actor(*permissions: str) -> AccessProfile:
    return AccessProfile(
        status="ACTIVO",
        user_id=str(uuid.uuid4()),
        roles=frozenset({"ADMINISTRADOR"}),
        permissions=frozenset(permissions),
    )


ADMIN = _actor(PERM_MANAGE_OFFICES)


@pytest.fixture
def store() -> FakeOfficeStore:
    return FakeOfficeStore()


@pytest.fixture
def service(store: FakeOfficeStore) -> OfficeAdminService:
    return OfficeAdminService(store=store, clock=lambda: NOW)


def _create_sede(service: OfficeAdminService, nombre: str = "Sede Norte") -> SedeDetail:
    return service.create_sede(ADMIN, nombre, "Av. del Parque 480")


def test_creates_a_sede_with_trimmed_name_and_optional_address(service):
    sede = service.create_sede(ADMIN, "  Sede Sur  ", None)
    assert sede.nombre == "Sede Sur"
    assert sede.direccion is None
    assert sede.activo is True
    assert sede.consultorios == ()


def test_blank_or_too_long_names_are_refused(service):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "   ", None)
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "x" * 151, None)


def test_address_is_limited_to_300_characters(service):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "Sede", "d" * 301)


def test_users_without_the_permission_are_refused(service):
    with pytest.raises(Forbidden):
        service.list_sedes(_actor("horario.generar"))
    with pytest.raises(Forbidden):
        service.create_sede(_actor(), "Sede", None)


def test_list_includes_inactive_sedes_and_their_consultorios(service, store):
    sede = _create_sede(service)
    service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    service.update_sede(ADMIN, sede.id, {"activo": False})
    listed = service.list_sedes(ADMIN)
    assert len(listed) == 1
    assert listed[0].activo is False
    assert [c.nombre for c in listed[0].consultorios] == ["Consultorio 1"]


def test_updating_only_sent_fields_keeps_the_rest(service):
    sede = _create_sede(service)
    updated = service.update_sede(ADMIN, sede.id, {"nombre": "Norte"})
    assert updated.nombre == "Norte"
    assert updated.direccion == "Av. del Parque 480"


def test_a_null_direccion_clears_it(service):
    sede = _create_sede(service)
    assert service.update_sede(ADMIN, sede.id, {"direccion": None}).direccion is None


def test_null_nombre_or_activo_is_refused(service):
    sede = _create_sede(service)
    with pytest.raises(ValidationFailed):
        service.update_sede(ADMIN, sede.id, {"nombre": None})
    with pytest.raises(ValidationFailed):
        service.update_sede(ADMIN, sede.id, {"activo": None})


def test_unknown_sede_is_not_found(service):
    with pytest.raises(NotFound):
        service.update_sede(ADMIN, str(uuid.uuid4()), {"nombre": "x"})


def test_deactivating_a_sede_is_refused_while_a_consultorio_has_future_bookings(service, store):
    sede = _create_sede(service)
    office = service.create_consultorio(ADMIN, sede.id, "Consultorio 1").consultorios[0]
    store.booked.add(office.id)
    with pytest.raises(Conflict) as refused:
        service.update_sede(ADMIN, sede.id, {"activo": False})
    assert refused.value.code == CITAS_FUTURAS
    assert store.sedes[sede.id]["activo"] is True


def test_deactivating_a_sede_without_future_bookings_works(service):
    sede = _create_sede(service)
    service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    assert service.update_sede(ADMIN, sede.id, {"activo": False}).activo is False


def test_a_consultorio_is_created_in_an_active_sede_only(service):
    sede = _create_sede(service)
    service.update_sede(ADMIN, sede.id, {"activo": False})
    with pytest.raises(ValidationFailed) as refused:
        service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    assert refused.value.code == SEDE_INACTIVA


def test_duplicate_consultorio_name_in_the_same_sede_is_a_conflict(service):
    sede = _create_sede(service)
    service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    with pytest.raises(Conflict) as refused:
        service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    assert refused.value.code == CONSULTORIO_DUPLICADO


def test_the_same_consultorio_name_is_allowed_in_another_sede(service):
    north = _create_sede(service, "Norte")
    south = _create_sede(service, "Sur")
    service.create_consultorio(ADMIN, north.id, "Consultorio 1")
    assert len(service.create_consultorio(ADMIN, south.id, "Consultorio 1").consultorios) == 1


def test_consultorio_deactivation_is_refused_with_future_bookings(service, store):
    sede = _create_sede(service)
    office = service.create_consultorio(ADMIN, sede.id, "Consultorio 1").consultorios[0]
    store.booked.add(office.id)
    with pytest.raises(Conflict) as refused:
        service.update_consultorio(ADMIN, office.id, {"activo": False})
    assert refused.value.code == CITAS_FUTURAS


def test_reactivating_a_consultorio_needs_an_active_sede(service, store):
    sede = _create_sede(service)
    office = service.create_consultorio(ADMIN, sede.id, "Consultorio 1").consultorios[0]
    service.update_consultorio(ADMIN, office.id, {"activo": False})
    service.update_sede(ADMIN, sede.id, {"activo": False})
    with pytest.raises(ValidationFailed) as refused:
        service.update_consultorio(ADMIN, office.id, {"activo": True})
    assert refused.value.code == SEDE_INACTIVA


def test_consultorio_rename_to_an_existing_name_is_a_conflict(service):
    sede = _create_sede(service)
    service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    second = service.create_consultorio(ADMIN, sede.id, "Consultorio 2").consultorios[1]
    with pytest.raises(Conflict):
        service.update_consultorio(ADMIN, second.id, {"nombre": "Consultorio 1"})


def test_consultorio_update_of_unknown_id_is_not_found(service):
    with pytest.raises(NotFound):
        service.update_consultorio(ADMIN, str(uuid.uuid4()), {"nombre": "x"})

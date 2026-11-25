"""Edge and hostile-input cases for sedes and consultorios administration (no database).

Covers: data limits in UTF-16 units, control characters, injection-looking text stored as data,
type coercion refused at the boundary, mass assignment, permission evasion, forged or missing
credentials, and large batches of consultorios.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.scheduling.application.office_admin import (
    CITAS_FUTURAS,
    SEDE_INACTIVA,
    OfficeAdminService,
)
from medicalbooking.modules.scheduling.domain.policies import (
    PERM_GENERATE_SLOTS,
)
from medicalbooking.modules.scheduling.presentation.routes import (
    CreateConsultorioRequest,
    CreateSedeRequest,
    UpdateConsultorioRequest,
    UpdateSedeRequest,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed
from tests.unit.test_office_admin import ADMIN, NOW, FakeOfficeStore, _actor

SQL_INJECTION = "Sede'); DROP TABLE sedes; --"
XSS = "<script>alert(1)</script>"


@pytest.fixture
def store() -> FakeOfficeStore:
    return FakeOfficeStore()


@pytest.fixture
def service(store: FakeOfficeStore) -> OfficeAdminService:
    return OfficeAdminService(store=store, clock=lambda: NOW)


# ---------- data limits (SQL Server counts UTF-16 units) ----------


def test_sede_name_of_150_ascii_characters_is_accepted(service):
    assert len(service.create_sede(ADMIN, "a" * 150, None).nombre) == 150


def test_sede_name_of_151_ascii_characters_is_refused(service):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "a" * 151, None)


def test_emoji_counts_as_two_units_so_80_emoji_do_not_fit_nvarchar_80(service, store):
    # 80 code points but 160 UTF-16 units: SQL Server would truncate or fail.
    sede = service.create_sede(ADMIN, "Sede", None)
    with pytest.raises(ValidationFailed):
        service.create_consultorio(ADMIN, sede.id, "\U0001f600" * 80)


def test_40_emoji_fit_nvarchar_80_exactly(service):
    sede = service.create_sede(ADMIN, "Sede", None)
    assert len(service.create_consultorio(ADMIN, sede.id, "\U0001f600" * 40).consultorios) == 1


def test_direccion_at_the_300_unit_limit_is_accepted_and_301_refused(service):
    assert service.create_sede(ADMIN, "Sede", "d" * 300).direccion == "d" * 300
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "Sede", "d" * 301)


def test_whitespace_only_direccion_is_stored_as_null(service):
    assert service.create_sede(ADMIN, "Sede", "   \t  ").direccion is None


def test_surrounding_whitespace_is_trimmed_before_the_length_check(service):
    assert service.create_sede(ADMIN, "  Sede  ", None).nombre == "Sede"


# ---------- hostile text ----------


@pytest.mark.parametrize("bad", ["Sede\x00", "Sede\nNueva", "Sede\rX", "Sede\x1b[31m", "Sede\x7f"])
def test_control_characters_are_refused(service, bad):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, bad, None)


def test_injection_looking_name_is_stored_as_plain_data(service, store):
    sede = service.create_sede(ADMIN, SQL_INJECTION, None)
    assert sede.nombre == SQL_INJECTION
    assert store.sedes[sede.id]["nombre"] == SQL_INJECTION


def test_markup_is_stored_as_text_not_interpreted(service):
    assert service.create_sede(ADMIN, XSS, None).nombre == XSS


def test_unicode_names_are_kept(service):
    assert service.create_sede(ADMIN, "Clínica Ñandú 北京", None).nombre == "Clínica Ñandú 北京"


@pytest.mark.parametrize("bad", [123, 1.5, ["Sede"], {"n": "Sede"}, True])
def test_non_string_names_are_refused_by_the_service(service, bad):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, bad, None)


def test_non_string_direccion_is_refused(service):
    with pytest.raises(ValidationFailed):
        service.create_sede(ADMIN, "Sede", 42)


# ---------- type coercion refused at the request boundary ----------


@pytest.mark.parametrize("value", ["false", "true", 0, 1, "yes", []])
def test_activo_must_be_a_real_boolean_at_the_boundary(value):
    # A null activo is a valid model value (it means "not sent" with exclude_unset); the service
    # refuses it, see test_null_nombre_or_activo_is_refused in test_office_admin.py.
    with pytest.raises(ValidationError):
        UpdateSedeRequest(activo=value)
    with pytest.raises(ValidationError):
        UpdateConsultorioRequest(activo=value)


def test_nombre_must_be_a_real_string_at_the_boundary():
    with pytest.raises(ValidationError):
        CreateSedeRequest(nombre=123)
    with pytest.raises(ValidationError):
        UpdateSedeRequest(nombre=["x"])
    with pytest.raises(ValidationError):
        CreateConsultorioRequest(nombre=None)


# ---------- mass assignment ----------


@pytest.mark.parametrize(
    "extra",
    [
        {"zona_horaria": "UTC"},
        {"id": str(uuid.uuid4())},
        {"sede_id": str(uuid.uuid4())},
        {"consultorios": []},
    ],
)
def test_unknown_or_privileged_fields_are_refused_on_every_write_model(extra):
    for model, base in [
        (CreateSedeRequest, {"nombre": "Sede"}),
        (UpdateSedeRequest, {"nombre": "Sede"}),
        (CreateConsultorioRequest, {"nombre": "Consultorio"}),
        (UpdateConsultorioRequest, {"nombre": "Consultorio"}),
    ]:
        with pytest.raises(ValidationError):
            model(**base, **extra)


def test_update_changes_only_the_fields_sent(service):
    sede = service.create_sede(ADMIN, "Sede", "Av. 1")
    model = UpdateSedeRequest(activo=False)
    changes = model.model_dump(exclude_unset=True)
    assert changes == {"activo": False}
    updated = service.update_sede(ADMIN, sede.id, changes)
    assert updated.nombre == "Sede"
    assert updated.direccion == "Av. 1"
    assert updated.activo is False


def test_sending_null_direccion_clears_it_but_null_nombre_is_refused(service):
    sede = service.create_sede(ADMIN, "Sede", "Av. 1")
    assert service.update_sede(ADMIN, sede.id, {"direccion": None}).direccion is None
    with pytest.raises(ValidationFailed):
        service.update_sede(ADMIN, sede.id, {"nombre": None})


# ---------- permission evasion ----------


@pytest.mark.parametrize(
    "permission",
    [
        PERM_GENERATE_SLOTS,
        "SEDES.ADMINISTRAR",
        "sedes.administrar ",
        "sedes.*",
        "citas.reservar",
        "",
    ],
)
def test_near_miss_or_unrelated_permissions_are_refused(service, permission):
    actor = _actor(permission)
    with pytest.raises(Forbidden):
        service.list_sedes(actor)
    with pytest.raises(Forbidden):
        service.create_sede(actor, "Sede", None)


def test_no_permission_cannot_change_or_deactivate_anything(service, store):
    sede = service.create_sede(ADMIN, "Sede", None)
    nobody = _actor()
    with pytest.raises(Forbidden):
        service.update_sede(nobody, sede.id, {"activo": False})
    assert store.sedes[sede.id]["activo"] is True


def test_refused_writes_leave_no_partial_state(service, store):
    sede = service.create_sede(ADMIN, "Sede", None)
    before = dict(store.sedes[sede.id])
    with pytest.raises(ValidationFailed):
        service.update_sede(ADMIN, sede.id, {"nombre": "Nuevo", "activo": "false"})
    assert store.sedes[sede.id] == before


# ---------- business edges ----------


def test_consultorio_in_missing_sede_is_not_found(service):
    with pytest.raises(NotFound):
        service.create_consultorio(ADMIN, str(uuid.uuid4()), "Consultorio")


def test_consultorio_under_inactive_sede_is_refused_with_its_code(service):
    sede = service.create_sede(ADMIN, "Sede", None)
    service.update_sede(ADMIN, sede.id, {"activo": False})
    with pytest.raises(ValidationFailed) as refused:
        service.create_consultorio(ADMIN, sede.id, "Consultorio")
    assert refused.value.code == SEDE_INACTIVA


def test_deactivating_an_already_inactive_consultorio_does_not_run_the_guard(service, store):
    sede = service.create_sede(ADMIN, "Sede", None)
    office = service.create_consultorio(ADMIN, sede.id, "C1").consultorios[0]
    service.update_consultorio(ADMIN, office.id, {"activo": False})
    store.booked.add(office.id)  # would block if the guard ran
    updated = service.update_consultorio(ADMIN, office.id, {"activo": False, "nombre": "C1 bis"})
    assert updated.activo is False
    assert updated.nombre == "C1 bis"


def test_a_guarded_deactivation_changes_nothing(service, store):
    sede = service.create_sede(ADMIN, "Sede", None)
    office = service.create_consultorio(ADMIN, sede.id, "C1").consultorios[0]
    store.booked.add(office.id)
    with pytest.raises(Conflict) as refused:
        service.update_consultorio(ADMIN, office.id, {"activo": False, "nombre": "Otro"})
    assert refused.value.code == CITAS_FUTURAS
    assert store.consultorios[office.id]["nombre"] == "C1"
    assert store.consultorios[office.id]["activo"] is True


def test_guard_checks_every_consultorio_of_the_sede_in_one_call(service, store):
    sede = service.create_sede(ADMIN, "Sede", None)
    offices = [service.create_consultorio(ADMIN, sede.id, f"C{i}").consultorios for i in range(3)]
    ids = [o.id for o in offices[-1]]
    seen = []
    original = store.has_future_bookings

    def spy(consultorio_ids, now):
        seen.append(list(consultorio_ids))
        return original(consultorio_ids, now)

    store.has_future_bookings = spy
    service.update_sede(ADMIN, sede.id, {"activo": False})
    assert len(seen) == 1 and set(seen[0]) == set(ids)


def test_a_large_sede_with_many_consultorios_is_handled(service, store):
    """Overload: 500 consultorios in one sede; listing and a deactivation guard still work."""
    sede = service.create_sede(ADMIN, "Sede grande", None)
    for i in range(500):
        service.create_consultorio(ADMIN, sede.id, f"C{i:03d}")
    listed = service.list_sedes(ADMIN)
    assert len(listed[0].consultorios) == 500
    store.booked.add(listed[0].consultorios[-1].id)
    with pytest.raises(Conflict):
        service.update_sede(ADMIN, sede.id, {"activo": False})


def test_duplicate_name_check_is_exact_after_trimming(service):
    sede = service.create_sede(ADMIN, "Sede", None)
    service.create_consultorio(ADMIN, sede.id, "Consultorio 1")
    with pytest.raises(Conflict):
        service.create_consultorio(ADMIN, sede.id, "  Consultorio 1  ")
    # a different case is a different name for the store (SQL collation decides in production)
    assert len(service.create_consultorio(ADMIN, sede.id, "consultorio 1").consultorios) == 2


# ---------- credentials at the HTTP boundary (no database needed) ----------

KEY = "unit-signing-key-with-at-least-32-chars"


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None,
        sqlserver_port=1,
        sqlserver_database="unused",
        jwt_signing_key=SecretStr(KEY),
        stripe_secret_key=None,
    )
    return create_app(settings).test_client()


def _forged(key: str, **claims) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5), **claims}
    return jwt.encode(payload, key, algorithm="HS256")


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/admin/sedes", "get"),
        ("/api/v1/admin/sedes", "post"),
        (f"/api/v1/admin/sedes/{uuid.uuid4()}", "patch"),
        (f"/api/v1/admin/sedes/{uuid.uuid4()}/consultorios", "post"),
        (f"/api/v1/admin/consultorios/{uuid.uuid4()}", "patch"),
    ],
)
def test_every_office_route_requires_credentials(client, path, method):
    response = getattr(client, method)(path, json={"nombre": "x"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "bearer x", "Token abc", ""],
)
def test_malformed_authorization_headers_are_refused(client, header):
    response = client.get("/api/v1/admin/sedes", headers={"Authorization": header})
    assert response.status_code == 401


def test_token_signed_with_another_key_is_refused(client):
    token = _forged("another-key-that-is-long-enough-for-hs256!!")
    response = client.get("/api/v1/admin/sedes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_expired_token_is_refused(client):
    token = _forged(KEY, exp=datetime.now(UTC) - timedelta(minutes=1))
    response = client.get("/api/v1/admin/sedes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_unsigned_alg_none_token_is_refused(client):
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key=None,
        algorithm="none",
    )
    response = client.get("/api/v1/admin/sedes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_garbage_token_is_refused(client):
    for token in ["abc", "a.b.c", "....", "x" * 5000]:
        response = client.get("/api/v1/admin/sedes", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_invalid_uuid_in_path_never_reaches_the_service(client):
    # Routes use a uuid converter, so a malformed id is a 404 before any handler runs.
    response = client.patch(
        "/api/v1/admin/sedes/not-a-uuid",
        json={"activo": False},
        headers={"Authorization": "Bearer abc.def.ghi"},
    )
    assert response.status_code in (401, 404)
    assert response.status_code != 500

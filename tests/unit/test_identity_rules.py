from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    CredentialRecord,
    NewUser,
)
from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.modules.identity.domain.policies import can_read_patient
from medicalbooking.shared.domain.errors import Forbidden, Unauthenticated, ValidationFailed

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
PATIENT_PERMS = frozenset({"pacientes.leer_propio", "citas.reservar"})
DOCTOR_PERMS = frozenset({"pacientes.leer_con_acceso", "ordenes.crear"})
ADMIN_PERMS = frozenset({"pacientes.leer_cualquiera", "usuarios.administrar"})


# ---------- pure policy matrix ----------


@pytest.mark.parametrize(
    ("perms", "actor", "access", "expected"),
    [
        (PATIENT_PERMS, OWNER, False, True),  # owner reads own record
        (PATIENT_PERMS, OTHER, False, False),  # another patient is denied
        (PATIENT_PERMS, OTHER, True, False),  # a grant does not help without the capability
        (DOCTOR_PERMS, OTHER, True, True),  # clinician with active grant
        (DOCTOR_PERMS, OTHER, False, False),  # clinician without grant
        (frozenset(), OWNER, True, False),  # no capability at all, even if owner
        (ADMIN_PERMS, OTHER, False, True),  # administrator reads any record
    ],
)
def test_patient_read_policy_matrix(perms, actor, access, expected) -> None:
    assert can_read_patient(perms, actor, OWNER, access) is expected


# ---------- service with an in-memory directory ----------


@dataclass
class FakeDirectory:
    credentials: dict[str, CredentialRecord] = field(default_factory=dict)
    accounts: dict[str, AccessProfile] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    grants: set[tuple[str, str]] = field(default_factory=set)
    created: list[NewUser] = field(default_factory=list)

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None:
        return self.credentials.get(email)

    def load_access(self, user_id: str) -> AccessProfile | None:
        return self.accounts.get(user_id)

    def find_patient_owner(self, patient_id: str) -> str | None:
        return self.owners.get(patient_id)

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool:
        return (clinician_user_id, patient_id) in self.grants

    def create_user(self, new_user: NewUser) -> None:
        self.created.append(new_user)

    def register_failed_login(self, user_id: str, now) -> None:
        return None

    def reset_failed_logins(self, user_id: str) -> None:
        return None


class FakeHasher:
    def hash(self, password: str) -> str:
        return f"hash::{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls = getattr(self, "verify_calls", 0) + 1
        return password_hash == f"hash::{password}"


class FakeTokens:
    def issue(self, user_id: str) -> tuple[str, int]:
        return f"token-for-{user_id}", 900

    def subject_of(self, token: str) -> str:
        if not token.startswith("token-for-"):
            raise Unauthenticated("Authentication required")
        return token.removeprefix("token-for-")


def _service(
    directory: FakeDirectory | None = None,
) -> tuple[IdentityService, FakeDirectory, FakeHasher]:
    directory = directory or FakeDirectory()
    hasher = FakeHasher()
    return IdentityService(directory, hasher, FakeTokens()), directory, hasher


def _actor(user_id: str, perms: frozenset[str], status: str = "ACTIVO") -> AccessProfile:
    return AccessProfile(user_id=user_id, status=status, roles=frozenset(), permissions=perms)


def test_login_issues_token_for_correct_password() -> None:
    service, directory, _ = _service()
    directory.credentials["a@x.test"] = CredentialRecord(
        OWNER, "a@x.test", "ACTIVO", "hash::secret-pass-123"
    )

    issued = service.login("  A@X.test ", "secret-pass-123")

    assert issued.access_token == f"token-for-{OWNER}"
    assert issued.expires_in == 900


@pytest.mark.parametrize("status", ["INACTIVO", "BLOQUEADO"])
def test_login_rejects_inactive_accounts_with_the_same_message(status: str) -> None:
    service, directory, _ = _service()
    directory.credentials["a@x.test"] = CredentialRecord(
        OWNER, "a@x.test", status, "hash::pw-correct-123"
    )

    with pytest.raises(Unauthenticated) as error:
        service.login("a@x.test", "pw-correct-123")

    assert error.value.detail == "Invalid credentials"


def test_login_unknown_email_and_wrong_password_look_identical() -> None:
    service, directory, _ = _service()
    directory.credentials["a@x.test"] = CredentialRecord(
        OWNER, "a@x.test", "ACTIVO", "hash::real-pass-123"
    )

    with pytest.raises(Unauthenticated) as unknown:
        service.login("missing@x.test", "anything")
    with pytest.raises(Unauthenticated) as wrong:
        service.login("a@x.test", "wrong-pass-123")

    assert unknown.value.detail == wrong.value.detail == "Invalid credentials"


def test_unknown_account_still_runs_a_password_verification() -> None:
    service, _, hasher = _service()
    calls_before = getattr(hasher, "verify_calls", 0)

    with pytest.raises(Unauthenticated):
        service.login("nobody@x.test", "whatever-123456")

    assert getattr(hasher, "verify_calls", 0) == calls_before + 1


def test_authenticate_token_reads_live_status() -> None:
    service, directory, _ = _service()
    directory.accounts[OWNER] = _actor(OWNER, PATIENT_PERMS, status="BLOQUEADO")

    with pytest.raises(Unauthenticated):
        service.authenticate_token(f"token-for-{OWNER}")


def test_patient_lookup_hides_missing_and_forbidden_identically() -> None:
    service, directory, _ = _service()
    directory.owners["p-own"] = OWNER
    directory.owners["p-other"] = OTHER
    actor = _actor(OWNER, PATIENT_PERMS)

    with pytest.raises(Forbidden) as missing:
        service.get_patient_profile(actor, "p-missing")
    with pytest.raises(Forbidden) as other:
        service.get_patient_profile(actor, "p-other")

    assert missing.value.detail == other.value.detail
    assert service.get_patient_profile(actor, "p-own") == OWNER


def test_create_user_requires_administrator() -> None:
    service, directory, _ = _service()

    with pytest.raises(Forbidden):
        service.create_user(
            _actor(OWNER, PATIENT_PERMS),
            email="n@x.test",
            password="long-enough-pass",
            first_name="N",
            last_name="X",
            roles=["PACIENTE"],
        )
    assert directory.created == []


def test_create_user_enforces_password_length_and_role_domain() -> None:
    service, _, _ = _service()
    admin = _actor(OWNER, ADMIN_PERMS)

    with pytest.raises(ValidationFailed, match="at least 12"):
        service.create_user(
            admin,
            email="n@x.test",
            password="short",
            first_name="N",
            last_name="X",
            roles=["PACIENTE"],
        )
    with pytest.raises(ValidationFailed, match="Roles must be"):
        service.create_user(
            admin,
            email="n@x.test",
            password="long-enough-pass",
            first_name="N",
            last_name="X",
            roles=["ROOT"],
        )
    with pytest.raises(ValidationFailed, match="registro_profesional"):
        # A doctor cannot exist without a professional registration.
        service.create_user(
            admin,
            email="n@x.test",
            password="long-enough-pass",
            first_name="N",
            last_name="X",
            roles=["MEDICO"],
        )


def test_create_user_refuses_a_doctor_with_a_blank_registration() -> None:
    service, directory, _ = _service()

    with pytest.raises(ValidationFailed, match="registro_profesional"):
        service.create_user(
            _actor(OWNER, ADMIN_PERMS),
            email="m@x.test",
            password="long-enough-pass",
            first_name="M",
            last_name="X",
            roles=["MEDICO"],
            professional_registration="   ",
        )
    assert directory.created == []


def test_create_user_passes_the_trimmed_registration_for_a_doctor() -> None:
    service, directory, _ = _service()

    service.create_user(
        _actor(OWNER, ADMIN_PERMS),
        email="m@x.test",
        password="long-enough-pass",
        first_name="M",
        last_name="X",
        roles=["MEDICO"],
        professional_registration=" MP-1234 ",
    )

    created = directory.created[0]
    assert created.roles == ("MEDICO",)
    assert created.professional_registration == "MP-1234"


def test_create_user_ignores_the_registration_for_other_roles() -> None:
    service, directory, _ = _service()

    service.create_user(
        _actor(OWNER, ADMIN_PERMS),
        email="p@x.test",
        password="long-enough-pass",
        first_name="P",
        last_name="X",
        roles=["PACIENTE"],
        professional_registration="MP-1234",
    )

    assert directory.created[0].professional_registration is None


def test_create_user_stores_hash_never_the_plain_password() -> None:
    service, directory, _ = _service()

    user_id = service.create_user(
        _actor(OWNER, ADMIN_PERMS),
        email="Nuevo@X.test",
        password="long-enough-pass",
        first_name="N",
        last_name="X",
        roles=["PACIENTE", "PACIENTE"],
    )

    created = directory.created[0]
    assert created.user_id == user_id
    assert created.email == "nuevo@x.test"
    assert created.password_hash == "hash::long-enough-pass"
    assert created.roles == ("PACIENTE",)
    assert uuid.UUID(user_id)

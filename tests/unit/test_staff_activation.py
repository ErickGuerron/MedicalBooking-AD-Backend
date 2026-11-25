"""Staff accounts: created inactive, activated once through an emailed link, with the person's password.

Checked with an in-memory directory and a recording mailer. The SQL side is covered by the
integration suite; these tests pin the behaviour the use cases promise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile, MailDeliveryError, NewUser
from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.modules.identity.domain.policies import PERM_USERS_ADMIN
from medicalbooking.modules.identity.infrastructure.email import render_staff_activation_email
from medicalbooking.shared.domain.errors import ValidationFailed

ADMIN = AccessProfile(
    user_id="admin-1",
    status="ACTIVO",
    roles=frozenset({"ADMINISTRADOR"}),
    permissions=frozenset({PERM_USERS_ADMIN}),
)
BENEFITS = (("Agenda", "Revisa tus citas."), ("Propuestas", "Valida propuestas."), ("Horario", "Declara horario."))
STRONG = "Contraseña-Segura-2026"


class Hasher:
    def hash(self, password: str) -> str:
        return "hash:" + password

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "hash:" + password


class Tokens:
    def issue(self, user_id: str) -> tuple[str, int]:
        return "t", 900

    def subject_of(self, token: str) -> str:
        return token


@dataclass
class Directory:
    users: dict[str, NewUser] = field(default_factory=dict)
    tokens: dict[str, dict] = field(default_factory=dict)
    activated: list[str] = field(default_factory=list)

    def create_pending_staff(self, new_user: NewUser, token_hash: str, valid_hours: int) -> None:
        self.users[new_user.user_id] = new_user
        self.tokens[token_hash] = {"user": new_user.user_id, "used": False, "hours": valid_hours}

    def verification_state(self, token_hash: str) -> str:
        record = self.tokens.get(token_hash)
        if record is None:
            return "INVALID"
        return "USED" if record["used"] else "VALID"

    def activate_staff(self, token_hash: str, password_hash: str) -> bool:
        record = self.tokens.get(token_hash)
        if record is None or record["used"]:
            return False
        user = self.users[record["user"]]
        if user.status != "INACTIVO" or "PACIENTE" in user.roles:
            return False
        record["used"] = True
        self.users[user.user_id] = NewUser(
            user_id=user.user_id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            password_hash=password_hash,
            roles=user.roles,
            status="ACTIVO",
            professional_registration=user.professional_registration,
        )
        self.activated.append(user.user_id)
        return True

    # Unused by these tests; present so the fake satisfies what the service may call.
    def find_credentials_by_email(self, email: str):  # pragma: no cover
        return None


@dataclass
class Mailer:
    sent: list[dict] = field(default_factory=list)
    fail: bool = False

    def send_staff_activation(self, to_email, first_name, link, **kwargs) -> None:
        if self.fail:
            raise MailDeliveryError("down")
        self.sent.append({"to": to_email, "name": first_name, "link": link, **kwargs})


def service(mailer: Mailer | None = None, directory: Directory | None = None) -> tuple[IdentityService, Directory]:
    directory = directory or Directory()
    svc = IdentityService(
        directory=directory,
        hasher=Hasher(),
        tokens=Tokens(),
        clock=lambda: datetime(2026, 11, 24, 12, 0, tzinfo=UTC),
        mailer=mailer,
        verification_hours=24,
        staff_activation_url="https://staff.example.test/",
    )
    return svc, directory


def create(svc: IdentityService, role: str = "MEDICO", registration: str | None = "QA-1") -> str:
    return svc.create_user(
        ADMIN,
        email="Medico@Example.TEST",
        first_name="Ana",
        last_name="Ruiz",
        roles=[role],
        professional_registration=registration,
    )


def test_staff_account_is_created_inactive_with_an_unusable_password() -> None:
    svc, directory = service(Mailer())
    user_id = create(svc)
    account = directory.users[user_id]
    assert account.status == "INACTIVO"
    assert account.email == "medico@example.test"
    assert account.password_hash.startswith("hash:")
    assert account.password_hash != "hash:" + STRONG


def test_activation_email_carries_a_single_use_link_to_the_angular_page() -> None:
    mailer = Mailer()
    svc, _ = service(mailer)
    create(svc)
    sent = mailer.sent[0]
    assert sent["link"].startswith("https://staff.example.test/activar-cuenta?token=")
    assert sent["role_label"] == "médico"
    assert sent["expiry_hours"] == 24
    assert len(sent["benefits"]) == 3


def test_activation_with_a_valid_link_sets_the_password_and_activates_once() -> None:
    mailer = Mailer()
    svc, directory = service(mailer)
    user_id = create(svc)
    token = mailer.sent[0]["link"].split("token=")[1]
    svc.activate_staff_account(token, STRONG)
    assert directory.users[user_id].status == "ACTIVO"
    assert directory.users[user_id].password_hash == "hash:" + STRONG
    with pytest.raises(ValidationFailed):
        svc.activate_staff_account(token, STRONG)


def test_activation_rejects_short_passwords_and_unknown_tokens() -> None:
    mailer = Mailer()
    svc, _ = service(mailer)
    create(svc)
    token = mailer.sent[0]["link"].split("token=")[1]
    with pytest.raises(ValidationFailed):
        svc.activate_staff_account(token, "corta")
    with pytest.raises(ValidationFailed):
        svc.activate_staff_account("no-existe", STRONG)


def test_mail_failure_keeps_the_account_inactive_and_does_not_fail_creation() -> None:
    svc, directory = service(Mailer(fail=True))
    user_id = create(svc)
    assert directory.users[user_id].status == "INACTIVO"


def test_medico_requires_a_professional_registration() -> None:
    svc, _ = service(Mailer())
    with pytest.raises(ValidationFailed):
        create(svc, registration=None)


def test_only_administrators_create_staff() -> None:
    svc, _ = service(Mailer())
    viewer = AccessProfile(user_id="x", status="ACTIVO", roles=frozenset(), permissions=frozenset())
    from medicalbooking.shared.domain.errors import Forbidden

    with pytest.raises(Forbidden):
        svc.create_user(viewer, email="a@b.test", first_name="A", last_name="B", roles=["PACIENTE"])


def test_one_template_renders_every_role_with_escaped_values() -> None:
    rendered = render_staff_activation_email(
        "Ana <b>",
        "https://staff.example.test/activar-cuenta?token=abc",
        "https://cdn.example.test/assets/",
        role_label="laboratorio",
        expiry_hours=24,
        intro_list="Con tu cuenta podrás:",
        benefits=BENEFITS,
    )
    assert "$" not in rendered.html and "$" not in rendered.text
    assert "&lt;b&gt;" in rendered.html
    assert "laboratorio" in rendered.text
    assert "https://staff.example.test/activar-cuenta?token=abc" in rendered.text
    assert "24 horas" in rendered.text


def test_activation_requires_every_password_rule() -> None:
    mailer = Mailer()
    svc, _ = service(mailer)
    create(svc)
    token = mailer.sent[0]["link"].split("token=")[1]
    with pytest.raises(ValidationFailed):
        svc.activate_staff_account(token, "contraseña-segura-2026")
    with pytest.raises(ValidationFailed):
        svc.activate_staff_account(token, "Password1234")


def test_used_and_unknown_links_report_their_reason() -> None:
    mailer = Mailer()
    svc, _ = service(mailer)
    create(svc)
    token = mailer.sent[0]["link"].split("token=")[1]
    assert svc.activation_status(token) == "VALID"
    svc.activate_staff_account(token, STRONG)
    assert svc.activation_status(token) == "USED"
    with pytest.raises(ValidationFailed) as used:
        svc.activate_staff_account(token, STRONG)
    assert used.value.code == "USED"
    assert svc.activation_status("no-existe") == "INVALID"

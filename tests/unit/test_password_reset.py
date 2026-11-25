"""Password reset: the same single-use, expiring link rules as activation, for active accounts only.

Checked with the in-memory directory of the activation tests and a recording mailer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_staff_activation import STRONG, Directory, Mailer, service  # noqa: E402

from medicalbooking.modules.identity.application.ports import (
    CredentialRecord,
    MailDeliveryError,
    NewUser,
    UserSummary,
)
from medicalbooking.shared.domain.errors import ValidationFailed


class ResetDirectory(Directory):
    def __init__(self) -> None:
        super().__init__()
        self.resets: dict[str, dict] = {}

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None:
        for user in self.users.values():
            if user.email == email:
                return CredentialRecord(user_id=user.user_id, email=user.email, status=user.status, password_hash=None)
        return None

    def find_user(self, user_id: str) -> UserSummary | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        return UserSummary(
            user_id=user.user_id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            status=user.status,
            roles=tuple(sorted(user.roles)),
        )

    def create_password_reset(self, user_id: str, token_hash: str, valid_minutes: int) -> None:
        for record in self.resets.values():
            if record["user"] == user_id:
                record["used"] = True
        self.resets[token_hash] = {"user": user_id, "used": False}

    def password_reset_state(self, token_hash: str) -> str:
        record = self.resets.get(token_hash)
        if record is None:
            return "INVALID"
        return "USED" if record["used"] else "VALID"

    def consume_password_reset(self, token_hash: str, password_hash: str) -> bool:
        record = self.resets.get(token_hash)
        if record is None or record["used"]:
            return False
        user = self.users[record["user"]]
        if user.status != "ACTIVO":
            return False
        record["used"] = True
        self.users[user.user_id] = NewUser(
            user_id=user.user_id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            password_hash=password_hash,
            roles=user.roles,
            status=user.status,
            professional_registration=user.professional_registration,
        )
        return True


@dataclass
class ResetMailer(Mailer):
    resets: list[dict] = field(default_factory=list)

    def send_password_reset(self, to_email: str, first_name: str, link: str) -> None:
        if self.fail:
            raise MailDeliveryError("down")
        self.resets.append({"to": to_email, "link": link})


def active_staff(directory: ResetDirectory) -> None:
    directory.users["u1"] = NewUser(
        user_id="u1",
        email="ana@medilo.test",
        first_name="Ana",
        last_name="Test",
        password_hash="hash:viejo",
        roles=frozenset({"MEDICO"}),
        status="ACTIVO",
        professional_registration="QA-1",
    )


def test_a_link_is_sent_only_to_active_accounts() -> None:
    mailer = ResetMailer()
    svc, directory = service(mailer, ResetDirectory())
    svc.request_password_reset("nadie@medilo.test")
    assert mailer.resets == []
    active_staff(directory)
    svc.request_password_reset("ana@medilo.test")
    assert len(mailer.resets) == 1
    assert "/restablecer-contrasena?token=" in mailer.resets[0]["link"]


def test_the_link_is_single_use_and_refuses_with_its_reason() -> None:
    mailer = ResetMailer()
    svc, directory = service(mailer, ResetDirectory())
    active_staff(directory)
    svc.request_password_reset("ana@medilo.test")
    token = mailer.resets[0]["link"].split("token=")[1]
    assert svc.password_reset_status(token) == "VALID"
    svc.reset_password(token, STRONG)
    assert directory.users["u1"].password_hash == "hash:" + STRONG
    assert svc.password_reset_status(token) == "USED"
    with pytest.raises(ValidationFailed) as used:
        svc.reset_password(token, STRONG)
    assert used.value.code == "USED"


def test_a_new_request_retires_the_previous_link() -> None:
    mailer = ResetMailer()
    svc, directory = service(mailer, ResetDirectory())
    active_staff(directory)
    svc.request_password_reset("ana@medilo.test")
    first = mailer.resets[0]["link"].split("token=")[1]
    svc.request_password_reset("ana@medilo.test")
    assert svc.password_reset_status(first) == "USED"


def test_the_new_password_follows_the_rules_and_unknown_links_are_invalid() -> None:
    mailer = ResetMailer()
    svc, directory = service(mailer, ResetDirectory())
    active_staff(directory)
    svc.request_password_reset("ana@medilo.test")
    token = mailer.resets[0]["link"].split("token=")[1]
    with pytest.raises(ValidationFailed):
        svc.reset_password(token, "contraseña-segura-2026")
    with pytest.raises(ValidationFailed) as unknown:
        svc.reset_password("no-existe", STRONG)
    assert unknown.value.code == "INVALID"


def test_a_failing_mail_does_not_reveal_the_account() -> None:
    svc, directory = service(ResetMailer(fail=True), ResetDirectory())
    active_staff(directory)
    svc.request_password_reset("ana@medilo.test")
    svc.request_password_reset("nadie@medilo.test")

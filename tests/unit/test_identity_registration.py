"""Patient self-registration rules, with in-memory fakes for the directory and the mailer."""

from __future__ import annotations

import hashlib
import logging
import re

import pytest

from medicalbooking.modules.identity.application.ports import MailDeliveryError, NewUser
from medicalbooking.modules.identity.application.use_cases import (
    REGISTRATION_ACCEPTED,
    IdentityService,
)
from medicalbooking.shared.domain.errors import Conflict, ValidationFailed

PASSWORD = "synthetic-pass-123"


class FakeHasher:
    def hash(self, password: str) -> str:
        return "hash:" + password

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "hash:" + password


class FakeTokens:
    def issue(self, user_id: str) -> tuple[str, int]:
        return "token", 900

    def subject_of(self, token: str) -> str:
        return token


class FakeDirectory:
    def __init__(self) -> None:
        self.users: dict[str, NewUser] = {}
        self.verifications: dict[str, tuple[str, int]] = {}  # token_hash -> (user_id, hours)
        self.used: set[str] = set()
        self.active: set[str] = set()

    def create_pending_patient(self, new_user: NewUser, token_hash: str, valid_hours: int) -> None:
        if any(user.email == new_user.email for user in self.users.values()):
            raise Conflict("An account with this email already exists")
        self.users[new_user.user_id] = new_user
        self.verifications[token_hash] = (new_user.user_id, valid_hours)

    def consume_verification(self, token_hash: str) -> bool:
        if token_hash not in self.verifications or token_hash in self.used:
            return False
        self.used.add(token_hash)
        self.active.add(self.verifications[token_hash][0])
        return True


class FakeMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send_activation(self, to_email: str, first_name: str, link: str) -> None:
        if self.fail:
            raise MailDeliveryError("Activation email could not be sent")
        self.sent.append((to_email, first_name, link))


def _service(mailer: FakeMailer | None = None) -> tuple[IdentityService, FakeDirectory, FakeMailer]:
    directory = FakeDirectory()
    mailer = mailer or FakeMailer()
    service = IdentityService(
        directory=directory,
        hasher=FakeHasher(),
        tokens=FakeTokens(),
        mailer=mailer,
        public_base_url="https://api.example.test/",
        verification_hours=24,
    )
    return service, directory, mailer


def _register(service: IdentityService, email: str = "Paciente@Synthetic.test") -> str:
    return service.register_patient(
        email=email, password=PASSWORD, first_name="Ana", last_name="Sintetica"
    )


def _token_from(link: str) -> str:
    return re.search(r"token=([^&]+)$", link).group(1)


def test_registration_creates_an_inactive_patient_and_sends_a_link() -> None:
    service, directory, mailer = _service()

    message = _register(service)

    assert message == REGISTRATION_ACCEPTED
    (user,) = directory.users.values()
    assert user.status == "INACTIVO"
    assert user.roles == ("PACIENTE",)
    assert user.email == "paciente@synthetic.test"
    (to_email, first_name, link) = mailer.sent[0]
    assert to_email == "paciente@synthetic.test"
    assert link.startswith("https://api.example.test/api/v1/auth/verify-email?token=")


def test_only_the_hash_of_the_token_is_stored() -> None:
    service, directory, mailer = _service()
    _register(service)
    token = _token_from(mailer.sent[0][2])

    assert hashlib.sha256(token.encode()).hexdigest() in directory.verifications
    assert token not in directory.verifications


def test_existing_email_gets_the_same_answer_and_no_second_email() -> None:
    service, _, mailer = _service()
    first = _register(service)
    second = _register(service)

    assert first == second == REGISTRATION_ACCEPTED
    assert len(mailer.sent) == 1


def test_short_password_is_rejected_before_anything_is_stored() -> None:
    service, directory, mailer = _service()

    with pytest.raises(ValidationFailed):
        service.register_patient(
            email="a@synthetic.test", password="short", first_name="A", last_name="B"
        )
    assert directory.users == {}
    assert mailer.sent == []


def test_mail_failure_keeps_the_same_answer_and_never_logs_the_token(caplog) -> None:
    service, directory, _ = _service(FakeMailer(fail=True))
    caplog.set_level(logging.ERROR, logger="medicalbooking.identity")

    message = _register(service)

    assert message == REGISTRATION_ACCEPTED
    assert len(directory.users) == 1
    assert "token=" not in caplog.text
    assert all(token not in caplog.text for token in directory.verifications)


def test_verification_activates_the_account_once() -> None:
    service, directory, mailer = _service()
    _register(service)
    token = _token_from(mailer.sent[0][2])

    service.verify_email(token)

    assert len(directory.active) == 1
    with pytest.raises(ValidationFailed):
        service.verify_email(token)


def test_unknown_or_empty_tokens_get_one_generic_refusal() -> None:
    service, _, _ = _service()
    for token in ("", "x" * 201, "not-a-real-token"):
        with pytest.raises(ValidationFailed) as refusal:
            service.verify_email(token)
        assert str(refusal.value) == "The activation link is invalid, already used, or expired."


def test_registration_without_mailer_is_still_accepted_and_inactive() -> None:
    directory = FakeDirectory()
    service = IdentityService(directory=directory, hasher=FakeHasher(), tokens=FakeTokens())

    assert _register(service) == REGISTRATION_ACCEPTED
    (user,) = directory.users.values()
    assert user.status == "INACTIVO"

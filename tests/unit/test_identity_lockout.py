"""Login lockout after repeated failures, checked with an in-memory directory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    CredentialRecord,
    NewUser,
)
from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.modules.identity.domain.policies import (
    LOCK_DURATION,
    MAX_FAILED_LOGINS,
    failure_outcome,
    is_locked,
)
from medicalbooking.shared.domain.errors import Unauthenticated

START = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


class FakeHasher:
    def hash(self, password: str) -> str:
        return "hash:" + password

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "hash:" + password


class FakeTokens:
    def issue(self, user_id: str) -> tuple[str, int]:
        return "token-" + user_id, 900

    def subject_of(self, token: str) -> str:
        return token.removeprefix("token-")


@dataclass
class FakeDirectory:
    accounts: dict[str, dict] = field(default_factory=dict)

    def add(self, email: str, password: str) -> None:
        self.accounts[email] = {
            "user_id": "user-1",
            "status": "ACTIVO",
            "password_hash": "hash:" + password,
            "attempts": 0,
            "locked_until": None,
        }

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None:
        account = self.accounts.get(email)
        if account is None:
            return None
        return CredentialRecord(
            user_id=account["user_id"],
            email=email,
            status=account["status"],
            password_hash=account["password_hash"],
            failed_attempts=account["attempts"],
            locked_until=account["locked_until"],
        )

    def register_failed_login(self, user_id: str, now: datetime) -> None:
        for account in self.accounts.values():
            if account["user_id"] == user_id:
                attempts, locked_until = failure_outcome(
                    account["attempts"], account["locked_until"], now
                )
                account["attempts"], account["locked_until"] = attempts, locked_until

    def reset_failed_logins(self, user_id: str) -> None:
        for account in self.accounts.values():
            if account["user_id"] == user_id:
                account["attempts"], account["locked_until"] = 0, None

    def load_access(self, user_id: str) -> AccessProfile | None:
        return AccessProfile(
            user_id=user_id, status="ACTIVO", roles=frozenset(), permissions=frozenset()
        )

    def find_patient_owner(self, patient_id: str) -> str | None:
        return None

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool:
        return False

    def create_user(self, new_user: NewUser) -> None:
        raise NotImplementedError


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(clock: Clock) -> IdentityService:
    directory = FakeDirectory()
    directory.add("paciente@synthetic.test", "correct-password-1")
    return IdentityService(
        directory=directory, hasher=FakeHasher(), tokens=FakeTokens(), clock=clock
    )


def _fail(service: IdentityService, times: int) -> None:
    for _ in range(times):
        with pytest.raises(Unauthenticated):
            service.login("paciente@synthetic.test", "wrong-password-9")


def test_account_locks_after_the_maximum_failed_attempts(service: IdentityService) -> None:
    _fail(service, MAX_FAILED_LOGINS)

    with pytest.raises(Unauthenticated):
        service.login("paciente@synthetic.test", "correct-password-1")


def test_locked_account_answers_exactly_like_a_wrong_password(service: IdentityService) -> None:
    _fail(service, MAX_FAILED_LOGINS)

    with pytest.raises(Unauthenticated) as locked:
        service.login("paciente@synthetic.test", "correct-password-1")
    with pytest.raises(Unauthenticated) as wrong:
        service.login("paciente@synthetic.test", "wrong-password-9")

    assert locked.value.detail == wrong.value.detail == "Invalid credentials"


def test_account_unlocks_after_the_lock_duration(service: IdentityService, clock: Clock) -> None:
    _fail(service, MAX_FAILED_LOGINS)
    clock.now += LOCK_DURATION + timedelta(seconds=1)

    issued = service.login("paciente@synthetic.test", "correct-password-1")

    assert issued.access_token == "token-user-1"


def test_successful_login_resets_the_failure_counter(service: IdentityService) -> None:
    _fail(service, MAX_FAILED_LOGINS - 1)
    service.login("paciente@synthetic.test", "correct-password-1")
    _fail(service, MAX_FAILED_LOGINS - 1)

    # Four failures after a success do not lock the account: the counter restarted.
    assert service.login("paciente@synthetic.test", "correct-password-1").access_token


def test_failure_after_an_expired_lock_starts_a_new_window(
    service: IdentityService, clock: Clock
) -> None:
    _fail(service, MAX_FAILED_LOGINS)
    clock.now += LOCK_DURATION + timedelta(seconds=1)

    _fail(service, 1)

    # One failure after the lock expired is not a new lock: the window restarts at one.
    assert service.login("paciente@synthetic.test", "correct-password-1").access_token


def test_unknown_emails_do_not_create_counters_or_locks(service: IdentityService) -> None:
    for _ in range(MAX_FAILED_LOGINS + 3):
        with pytest.raises(Unauthenticated):
            service.login("nobody@synthetic.test", "any-password-1")

    assert service.login("paciente@synthetic.test", "correct-password-1").access_token


# ---------- pure policy ----------


def test_policy_locks_on_the_threshold_failure() -> None:
    now = START
    attempts, locked_until = failure_outcome(MAX_FAILED_LOGINS - 1, None, now)

    assert attempts == MAX_FAILED_LOGINS
    assert locked_until == now + LOCK_DURATION


def test_repeated_failures_after_an_expired_lock_lock_again() -> None:
    # The stored expired timestamp must be cleared by the first failure of the new window,
    # so later failures accumulate instead of restarting the count every time.
    now = START
    attempts, locked_until = MAX_FAILED_LOGINS, now - timedelta(minutes=1)
    locks = 0
    for _ in range(MAX_FAILED_LOGINS):
        attempts, locked_until = failure_outcome(attempts, locked_until, now)
        if locked_until is not None:
            locks += 1
    assert locks == 1
    assert attempts == MAX_FAILED_LOGINS


def test_policy_restarts_the_window_after_an_expired_lock() -> None:
    expired = START - timedelta(minutes=1)

    attempts, locked_until = failure_outcome(MAX_FAILED_LOGINS, expired, START)

    assert attempts == 1
    assert locked_until is None


def test_is_locked_only_inside_the_window() -> None:
    until = START + LOCK_DURATION

    assert is_locked(until, START)
    assert not is_locked(until, until)
    assert not is_locked(None, START)

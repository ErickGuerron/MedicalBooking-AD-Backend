"""Ports: interfaces the identity use cases depend on. Infrastructure implements them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CredentialRecord:
    user_id: str
    email: str
    status: str
    password_hash: str | None
    failed_attempts: int = 0
    locked_until: datetime | None = None


@dataclass(frozen=True)
class AccessProfile:
    user_id: str
    status: str
    roles: frozenset[str]
    permissions: frozenset[str]


@dataclass(frozen=True)
class UserSummary:
    """Administrator view of an account. Never carries credentials."""

    user_id: str
    email: str
    first_name: str
    last_name: str
    status: str
    roles: tuple[str, ...]


DEFAULT_APPOINTMENT_MINUTES = 30


@dataclass(frozen=True)
class NewUser:
    user_id: str
    email: str
    first_name: str
    last_name: str
    password_hash: str
    roles: tuple[str, ...]
    status: str = "ACTIVO"
    # Set only for a MEDICO: the repository then writes the clinician's profile (medicos) too.
    professional_registration: str | None = None


class MailDeliveryError(Exception):
    """The verification email could not be sent. The message never includes the token."""


class ActivationMailer(Protocol):
    def send_activation(self, to_email: str, first_name: str, link: str) -> None:
        """Send the account activation link. Raises MailDeliveryError on failure."""
        ...


class UserDirectory(Protocol):
    def create_pending_patient(self, new_user: NewUser, token_hash: str, valid_hours: int) -> None:
        """Create an INACTIVO patient and its verification token in one transaction.

        Raises Conflict when the email is already registered.
        """
        ...

    def consume_verification(self, token_hash: str) -> bool:
        """Activate the account behind a valid, unused, unexpired token. Single use."""
        ...

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None: ...

    def load_access(self, user_id: str) -> AccessProfile | None: ...

    def find_patient_owner(self, patient_id: str) -> str | None: ...

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool: ...

    def create_user(self, new_user: NewUser) -> None: ...

    def list_users(self, limit: int, offset: int) -> list[UserSummary]:
        """One page of accounts ordered by email, with their roles. Never reads credentials."""
        ...

    def register_failed_login(self, user_id: str, now: datetime) -> None:
        """Count one failure atomically and lock the account when the threshold is reached."""
        ...

    def reset_failed_logins(self, user_id: str) -> None: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class TokenIssuer(Protocol):
    def issue(self, user_id: str) -> tuple[str, int]: ...

    def subject_of(self, token: str) -> str: ...

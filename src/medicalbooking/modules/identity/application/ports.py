"""Ports: interfaces the identity use cases depend on. Infrastructure implements them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
    # Optional demographics of a PACIENTE, stored on the patient row created with the account.
    sex: str | None = None
    birth_date: date | None = None
    # Patients: the identity document (required at self-registration) and the optional phone.
    document_type: str | None = None
    document_number: str | None = None
    phone: str | None = None


class MailDeliveryError(Exception):
    """The verification email could not be sent. The message never includes the token."""


class ActivationMailer(Protocol):
    def send_activation(self, to_email: str, first_name: str, link: str) -> None:
        """Send the account activation link. Raises MailDeliveryError on failure."""
        ...

    def send_password_reset(self, to_email: str, first_name: str, link: str) -> None:
        """Send the password reset link. Raises MailDeliveryError on failure."""
        ...

    def send_staff_activation(
        self,
        to_email: str,
        first_name: str,
        link: str,
        *,
        role_label: str,
        expiry_hours: int,
        intro_list: str,
        benefits: tuple[tuple[str, str], tuple[str, str], tuple[str, str]],
    ) -> None:
        """Send the staff activation link. Raises MailDeliveryError on failure."""
        ...


class UserDirectory(Protocol):
    def document_taken(self, document_type: str, document_number: str) -> bool: ...

    def create_pending_patient(self, new_user: NewUser, token_hash: str, valid_hours: int) -> None:
        """Create an INACTIVO patient and its verification token in one transaction.

        Raises Conflict when the email is already registered.
        """
        ...

    def consume_verification(self, token_hash: str) -> bool:
        """Activate the account behind a valid, unused, unexpired token. Single use."""
        ...

    def create_pending_staff(
        self, new_user: NewUser, token_hash: str, valid_hours: int
    ) -> None: ...

    def verification_state(self, token_hash: str) -> str:
        """VALID, USED, EXPIRED or INVALID: why a link cannot be used now.
        Carries no account data."""
        ...

    def create_password_reset(self, user_id: str, token_hash: str, valid_minutes: int) -> None:
        """Store a new single-use reset link and retire the account's unused ones."""
        ...

    def password_reset_state(self, token_hash: str) -> str: ...

    def consume_password_reset(self, token_hash: str, password_hash: str) -> bool: ...

    def activate_staff(self, token_hash: str, password_hash: str) -> bool: ...

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None: ...

    def load_access(self, user_id: str) -> AccessProfile | None: ...

    def find_patient_owner(self, patient_id: str) -> str | None: ...

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool: ...

    def create_user(self, new_user: NewUser) -> None: ...

    def find_user(self, user_id: str) -> UserSummary | None:
        """One account's name, email, status and roles. Never reads credentials."""
        ...

    def list_users(self, limit: int, offset: int) -> list[UserSummary]:
        """One page of accounts ordered by email, with their roles. Never reads credentials."""
        ...

    def register_failed_login(self, user_id: str, now: datetime) -> None:
        """Count one failure atomically and lock the account when the threshold is reached."""
        ...

    def reset_failed_logins(self, user_id: str) -> None: ...

    def update_password_hash(self, user_id: str, password_hash: str) -> None: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class TokenIssuer(Protocol):
    def issue(self, user_id: str) -> tuple[str, int]: ...

    def subject_of(self, token: str) -> str: ...

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
class NewUser:
    user_id: str
    email: str
    first_name: str
    last_name: str
    password_hash: str
    roles: tuple[str, ...]


class UserDirectory(Protocol):
    def find_credentials_by_email(self, email: str) -> CredentialRecord | None: ...

    def load_access(self, user_id: str) -> AccessProfile | None: ...

    def find_patient_owner(self, patient_id: str) -> str | None: ...

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool: ...

    def create_user(self, new_user: NewUser) -> None: ...

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

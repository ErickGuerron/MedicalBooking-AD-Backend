"""Identity use cases. Business rules live here, not in routes or SQL."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    NewUser,
    PasswordHasher,
    TokenIssuer,
    UserDirectory,
)
from medicalbooking.modules.identity.domain.policies import (
    PERM_USERS_ADMIN,
    can_read_patient,
)
from medicalbooking.shared.domain.errors import Forbidden, Unauthenticated, ValidationFailed

ACTIVE = "ACTIVO"
# Roles that can be assigned through the API in E1. MEDICO needs a professional
# registration profile, which is out of scope for this slice.
ASSIGNABLE_ROLES = frozenset({"PACIENTE", "LABORATORISTA", "ADMINISTRADOR"})
MIN_PASSWORD_LENGTH = 12


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int


class IdentityService:
    def __init__(
        self,
        directory: UserDirectory,
        hasher: PasswordHasher,
        tokens: TokenIssuer,
    ) -> None:
        self._directory = directory
        self._hasher = hasher
        self._tokens = tokens
        # Verified against when the account is unknown, so response time does not
        # reveal whether an email is registered.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(32))

    def login(self, email: str, password: str) -> IssuedToken:
        record = self._directory.find_credentials_by_email(_normalize_email(email))
        if record is None or record.password_hash is None:
            self._hasher.verify(self._dummy_hash, password)
            raise Unauthenticated("Invalid credentials")
        if not self._hasher.verify(record.password_hash, password):
            raise Unauthenticated("Invalid credentials")
        if record.status != ACTIVE:
            raise Unauthenticated("Invalid credentials")
        token, expires_in = self._tokens.issue(record.user_id)
        return IssuedToken(access_token=token, expires_in=expires_in)

    def authenticate_token(self, token: str) -> AccessProfile:
        """Resolve a bearer token to the current, active account.

        Permissions are read on every request, so revoking a role or disabling an
        account takes effect without waiting for the token to expire.
        """
        subject = self._tokens.subject_of(token)
        profile = self._directory.load_access(subject)
        if profile is None or profile.status != ACTIVE:
            raise Unauthenticated("Authentication required")
        return profile

    def get_patient_profile(self, actor: AccessProfile, patient_id: str) -> str:
        """Return the patient's user id if the actor may read the patient.

        A missing patient and a forbidden patient produce the same response, so the
        API does not reveal which patient identifiers exist.
        """
        owner = self._directory.find_patient_owner(patient_id)
        has_access = owner is not None and self._directory.has_active_patient_access(
            actor.user_id, patient_id
        )
        if owner is None or not can_read_patient(
            actor.permissions, actor.user_id, owner, has_access
        ):
            raise Forbidden("Access to this resource is not allowed")
        return owner

    def create_user(
        self,
        actor: AccessProfile,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        roles: list[str],
    ) -> str:
        if PERM_USERS_ADMIN not in actor.permissions:
            raise Forbidden("Access to this resource is not allowed")
        normalized = _normalize_email(email)
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationFailed(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
        unique_roles = tuple(sorted(set(roles)))
        if not unique_roles or not set(unique_roles) <= ASSIGNABLE_ROLES:
            raise ValidationFailed(
                "Roles must be one or more of: " + ", ".join(sorted(ASSIGNABLE_ROLES))
            )

        user_id = str(uuid.uuid4())
        self._directory.create_user(
            NewUser(
                user_id=user_id,
                email=normalized,
                first_name=first_name,
                last_name=last_name,
                password_hash=self._hasher.hash(password),
                roles=unique_roles,
            )
        )
        return user_id


def _normalize_email(email: str) -> str:
    return email.strip().lower()

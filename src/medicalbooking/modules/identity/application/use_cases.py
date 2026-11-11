"""Identity use cases. Business rules live here, not in routes or SQL."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    ActivationMailer,
    MailDeliveryError,
    NewUser,
    PasswordHasher,
    TokenIssuer,
    UserDirectory,
    UserSummary,
)
from medicalbooking.modules.identity.domain.policies import (
    PERM_USERS_ADMIN,
    can_read_patient,
    is_locked,
    validate_page,
)
from medicalbooking.shared.domain.errors import (
    Conflict,
    Forbidden,
    Unauthenticated,
    ValidationFailed,
)

ACTIVE = "ACTIVO"
INACTIVE = "INACTIVO"
PATIENT_ROLE = "PACIENTE"
# Returned for every registration, whether or not the email already exists.
REGISTRATION_ACCEPTED = "If the address can receive an account, we sent an activation link to it."
VERIFICATION_FAILED = "The activation link is invalid, already used, or expired."
_log = logging.getLogger("medicalbooking.identity")
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
        clock: Callable[[], datetime] | None = None,
        mailer: ActivationMailer | None = None,
        public_base_url: str = "http://localhost:5000",
        verification_hours: int = 24,
    ) -> None:
        self._directory = directory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mailer = mailer
        self._public_base_url = public_base_url.rstrip("/")
        self._verification_hours = verification_hours
        # Verified against when the account is unknown, so response time does not
        # reveal whether an email is registered.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(32))

    def register_patient(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
    ) -> str:
        """Create an inactive patient and send an activation link.

        Always returns the same message. An existing email produces the same answer
        and the same work (password hashing), so the response does not reveal which
        addresses have accounts.
        """
        normalized = _normalize_email(email)
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationFailed(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
        password_hash = self._hasher.hash(password)
        token = secrets.token_urlsafe(32)
        new_user = NewUser(
            user_id=str(uuid.uuid4()),
            email=normalized,
            first_name=first_name,
            last_name=last_name,
            password_hash=password_hash,
            roles=(PATIENT_ROLE,),
            status=INACTIVE,
        )
        try:
            self._directory.create_pending_patient(
                new_user, _token_hash(token), self._verification_hours
            )
        except Conflict:
            return REGISTRATION_ACCEPTED
        if self._mailer is None:
            _log.error("Activation email not sent: mail is not configured")
            return REGISTRATION_ACCEPTED
        try:
            self._mailer.send_activation(
                normalized,
                first_name,
                f"{self._public_base_url}/api/v1/auth/verify-email?token={token}",
            )
        except MailDeliveryError as error:
            # The account stays inactive; the message carries no token or address.
            _log.error("Activation email failed: %s", error)
        return REGISTRATION_ACCEPTED

    def verify_email(self, token: str) -> None:
        """Activate the account behind a single-use token, or refuse with one generic message."""
        if not token or len(token) > 200:
            raise ValidationFailed(VERIFICATION_FAILED)
        if not self._directory.consume_verification(_token_hash(token)):
            raise ValidationFailed(VERIFICATION_FAILED)

    def login(self, email: str, password: str) -> IssuedToken:
        record = self._directory.find_credentials_by_email(_normalize_email(email))
        if record is None or record.password_hash is None:
            self._hasher.verify(self._dummy_hash, password)
            raise Unauthenticated("Invalid credentials")
        now = self._clock()
        password_ok = self._hasher.verify(record.password_hash, password)
        if is_locked(record.locked_until, now):
            # Same answer as a wrong password, and the same hashing cost: a locked
            # account reveals nothing and the correct password does not unlock it early.
            raise Unauthenticated("Invalid credentials")
        if not password_ok:
            self._directory.register_failed_login(record.user_id, now)
            raise Unauthenticated("Invalid credentials")
        if record.status != ACTIVE:
            raise Unauthenticated("Invalid credentials")
        self._directory.reset_failed_logins(record.user_id)
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

    def list_users(self, actor: AccessProfile, *, limit: int, offset: int) -> list[UserSummary]:
        if PERM_USERS_ADMIN not in actor.permissions:
            raise Forbidden("Access to this resource is not allowed")
        validate_page(limit, offset)
        return self._directory.list_users(limit, offset)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _token_hash(token: str) -> str:
    """Tokens are stored only as SHA-256 hashes, so a database leak does not expose live links."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

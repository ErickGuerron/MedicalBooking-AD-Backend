"""Identity use cases. Business rules live here, not in routes or SQL."""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
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
    validate_document,
    validate_page,
    validate_phone,
)
from medicalbooking.shared.domain.demographics import parse_birth_date, validate_sex
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
# Roles that an administrator can assign. A MEDICO also needs a professional registration,
# which create_user requires and stores in the same transaction as the account.
ASSIGNABLE_ROLES = frozenset({"PACIENTE", "LABORATORISTA", "ADMINISTRADOR", "MEDICO"})
PROFESSIONAL_ROLE = "MEDICO"

# Staff activation email: one template, with these values per role.
STAFF_ROLE_LABELS = {
    "MEDICO": "médico",
    "LABORATORISTA": "laboratorio",
    "ADMINISTRADOR": "administración",
    "PACIENTE": "paciente",
}
STAFF_INTRO = "Con tu cuenta podrás:"
STAFF_DEFAULT_BENEFITS = (
    ("Cuenta", "Accede al sistema de Medilo con tu rol."),
    ("Seguridad", "Tu sesión se cierra tras 15 minutos de inactividad."),
    ("Contraseña", "Solo tú conoces la contraseña que defines."),
)
STAFF_BENEFITS = {
    "MEDICO": (
        ("Agenda", "Revisa tus citas y pacientes."),
        ("Propuestas", "Valida las propuestas del motor clínico."),
        ("Horario", "Declara tu horario de atención."),
    ),
    "LABORATORISTA": (
        ("Órdenes", "Registra los resultados de cada prueba."),
        ("Informes", "Sube los informes en PDF."),
        ("Cola", "Atiende las órdenes pendientes."),
    ),
    "ADMINISTRADOR": (
        ("Usuarios", "Crea cuentas del personal y de pacientes."),
        ("Conocimiento", "Gestiona analitos y reglas clínicas."),
        ("Reportes", "Revisa cifras y el monitoreo."),
    ),
}
MIN_PASSWORD_LENGTH = 12
PASSWORD_RESET_MINUTES = 60


def staff_password_problems(password: str) -> list[str]:
    """Requirements a staff member's new password still misses;
    an empty list means it is acceptable."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isupper() for c in password):
        problems.append("an uppercase letter")
    if not any(c.islower() for c in password):
        problems.append("a lowercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(c in string.punctuation for c in password):
        problems.append("a symbol")
    return problems


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
        staff_activation_url: str = "http://localhost:4200",
        patient_verification_url: str | None = None,
    ) -> None:
        self._directory = directory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock or (lambda: datetime.now(UTC))
        self._mailer = mailer
        self._public_base_url = public_base_url.rstrip("/")
        self._verification_hours = verification_hours
        self._staff_activation_url = staff_activation_url.rstrip("/")
        # The link opens the patient portal, which shows the verification view and calls the API.
        self._patient_verification_url = (patient_verification_url or public_base_url).rstrip("/")
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
        document_type: str,
        document_number: str,
        phone: str | None = None,
        sex: str | None = None,
        birth_date: str | None = None,
    ) -> str:
        """Create an inactive patient and send an activation link.

        The identity document is required and must be new; the phone is optional.

        Always returns the same message. An existing email produces the same answer
        and the same work (password hashing), so the response does not reveal which
        addresses have accounts.
        """
        normalized = _normalize_email(email)
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationFailed(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
        # Invalid demographics are a 422 about the request itself, never about the email.
        parsed_sex = None if sex is None else validate_sex(sex)
        parsed_birth = (
            None if birth_date is None else parse_birth_date(birth_date, self._clock().date())
        )
        number = validate_document(document_type, document_number)
        parsed_phone = validate_phone(phone)
        if self._directory.document_taken(document_type, number):
            raise Conflict("Document already registered")
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
            sex=parsed_sex,
            birth_date=parsed_birth,
            document_type=document_type,
            document_number=number,
            phone=parsed_phone,
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
                f"{self._patient_verification_url}/#/verificar?token={token}",
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
            raise ValidationFailed(VERIFICATION_FAILED, code=self._refusal_code(token))

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
        first_name: str,
        last_name: str,
        roles: list[str],
        professional_registration: str | None = None,
    ) -> str:
        """Create a staff account that stays inactive until its owner sets a password.

        The administrator never chooses or sees a password. The account gets an unusable hash
        and a single-use activation link; the person defines the password through that link.
        """
        if PERM_USERS_ADMIN not in actor.permissions:
            raise Forbidden("Access to this resource is not allowed")
        normalized = _normalize_email(email)
        unique_roles = tuple(sorted(set(roles)))
        if not unique_roles or not set(unique_roles) <= ASSIGNABLE_ROLES:
            raise ValidationFailed(
                "Roles must be one or more of: " + ", ".join(sorted(ASSIGNABLE_ROLES))
            )
        registration = _professional_registration(unique_roles, professional_registration)

        user_id = str(uuid.uuid4())
        # Nobody knows this value, so sign-in stays impossible until activation sets the real hash.
        unusable_hash = self._hasher.hash(secrets.token_urlsafe(48))
        token = secrets.token_urlsafe(32)
        self._directory.create_pending_staff(
            NewUser(
                user_id=user_id,
                email=normalized,
                first_name=first_name,
                last_name=last_name,
                password_hash=unusable_hash,
                roles=unique_roles,
                status=INACTIVE,
                professional_registration=registration,
            ),
            _token_hash(token),
            self._verification_hours,
        )
        self._send_staff_activation(normalized, first_name, unique_roles[0], token)
        return user_id

    def activate_staff_account(self, token: str, password: str) -> None:
        """Set the password of a staff account and activate it, once, from its email link."""
        if not token or len(token) > 200:
            raise ValidationFailed(VERIFICATION_FAILED)
        problems = staff_password_problems(password)
        if problems:
            raise ValidationFailed("Password must have: " + ", ".join(problems))
        if not self._directory.activate_staff(_token_hash(token), self._hasher.hash(password)):
            raise ValidationFailed(VERIFICATION_FAILED, code=self._refusal_code(token))

    def activation_status(self, token: str) -> str:
        """Tells the activation pages why a link cannot be used,
        before the person types anything."""
        if not token or len(token) > 200:
            return "INVALID"
        return self._directory.verification_state(_token_hash(token))

    def request_password_reset(self, email: str) -> None:
        """Always answers the same, whatever the email. Only an active account receives a link."""
        record = self._directory.find_credentials_by_email(_normalize_email(email))
        if record is None or record.status != "ACTIVO":
            return
        user = self._directory.find_user(record.user_id)
        if user is None:
            return
        token = secrets.token_urlsafe(32)
        self._directory.create_password_reset(
            record.user_id, _token_hash(token), PASSWORD_RESET_MINUTES
        )
        if self._mailer is None:
            _log.error("Password reset email not sent: mail is not configured")
            return
        # Patients reset in the Flutter portal; staff reset in the Angular app.
        if "PACIENTE" in user.roles:
            link = f"{self._patient_verification_url}/#/restablecer?token={token}"
        else:
            link = f"{self._staff_activation_url}/restablecer-contrasena?token={token}"
        try:
            self._mailer.send_password_reset(user.email, user.first_name, link)
        except MailDeliveryError:
            _log.error("Password reset email could not be delivered")

    def password_reset_status(self, token: str) -> str:
        if not token or len(token) > 200:
            return "INVALID"
        return self._directory.password_reset_state(_token_hash(token))

    def reset_password(self, token: str, password: str) -> None:
        if not token or len(token) > 200:
            raise ValidationFailed(VERIFICATION_FAILED, code="INVALID")
        problems = staff_password_problems(password)
        if problems:
            raise ValidationFailed("Password must have: " + ", ".join(problems))
        if not self._directory.consume_password_reset(
            _token_hash(token), self._hasher.hash(password)
        ):
            state = self.password_reset_status(token)
            raise ValidationFailed(
                VERIFICATION_FAILED, code=state if state in {"USED", "EXPIRED"} else "INVALID"
            )

    def _refusal_code(self, token: str) -> str:
        """The reason behind a refused link: USED, EXPIRED or INVALID (never VALID)."""
        state = self.activation_status(token)
        return state if state in {"USED", "EXPIRED"} else "INVALID"

    def _send_staff_activation(self, email: str, first_name: str, role: str, token: str) -> None:
        if self._mailer is None:
            _log.error("Staff activation email not sent: mail is not configured")
            return
        link = f"{self._staff_activation_url}/activar-cuenta?token={token}"
        try:
            self._mailer.send_staff_activation(
                email,
                first_name,
                link,
                role_label=STAFF_ROLE_LABELS.get(role, role.lower()),
                expiry_hours=self._verification_hours,
                intro_list=STAFF_INTRO,
                benefits=STAFF_BENEFITS.get(role, STAFF_DEFAULT_BENEFITS),
            )
        except MailDeliveryError as error:
            # The account stays inactive and the message carries no token or address.
            _log.error("Staff activation email failed: %s", error)

    def change_password(self, actor: AccessProfile, current: str, new: str) -> None:
        """The signed-in account replaces its password after proving the current one.

        A wrong current password is a 403, never a 401: the portal signs out on 401. It counts as
        a failed login, so guessing it locks the account like a failed sign-in does.
        """
        profile = self._directory.find_user(actor.user_id)
        if profile is None:
            raise Unauthenticated("Invalid credentials")
        record = self._directory.find_credentials_by_email(profile.email)
        if record is None or record.password_hash is None:
            raise Unauthenticated("Invalid credentials")
        now = self._clock()
        if is_locked(record.locked_until, now):
            raise Forbidden("La contraseña actual no es correcta")
        if not self._hasher.verify(record.password_hash, current):
            self._directory.register_failed_login(actor.user_id, now)
            raise Forbidden("La contraseña actual no es correcta")
        if new == current:
            raise ValidationFailed("La nueva contraseña debe ser distinta de la actual")
        self._directory.update_password_hash(actor.user_id, self._hasher.hash(new))

    def own_profile(self, actor: AccessProfile) -> UserSummary:
        """The authenticated account's name and email, for the greeting and the account screen."""
        profile = self._directory.find_user(actor.user_id)
        if profile is None:
            raise Unauthenticated("Authentication required")
        return profile

    def list_users(self, actor: AccessProfile, *, limit: int, offset: int) -> list[UserSummary]:
        if PERM_USERS_ADMIN not in actor.permissions:
            raise Forbidden("Access to this resource is not allowed")
        validate_page(limit, offset)
        return self._directory.list_users(limit, offset)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _professional_registration(roles: tuple[str, ...], registration: str | None) -> str | None:
    """Required for a MEDICO and kept only for a MEDICO; other roles ignore the field."""
    if PROFESSIONAL_ROLE not in roles:
        return None
    value = (registration or "").strip()
    if not value:
        raise ValidationFailed("A MEDICO account needs a registro_profesional")
    return value


def _token_hash(token: str) -> str:
    """Tokens are stored only as SHA-256 hashes, so a database leak does not expose live links."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

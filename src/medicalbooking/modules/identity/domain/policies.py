"""Pure authorization and login-protection rules for the identity module (no I/O)."""

from __future__ import annotations

from datetime import datetime, timedelta

from medicalbooking.shared.domain.errors import ValidationFailed

# Brute-force protection (spec section 10). Five consecutive failures lock the account
# for fifteen minutes; a successful login clears the counter.
MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)

PERM_PATIENT_READ_OWN = "pacientes.leer_propio"
PERM_PATIENT_READ_WITH_ACCESS = "pacientes.leer_con_acceso"
PERM_PATIENT_READ_ANY = "pacientes.leer_cualquiera"
PERM_USERS_ADMIN = "usuarios.administrar"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_PAGE_OFFSET = 2_147_483_647  # SQL Server OFFSET takes an INT; larger values fail as a 500


def validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValidationFailed(f"'limit' must be between 1 and {MAX_PAGE_SIZE}")
    if not 0 <= offset <= MAX_PAGE_OFFSET:
        raise ValidationFailed(f"'offset' must be between 0 and {MAX_PAGE_OFFSET}")


def can_read_patient(
    permissions: frozenset[str],
    actor_user_id: str,
    patient_owner_user_id: str,
    has_active_access: bool,
) -> bool:
    """A patient record is readable by an administrator, by its owner, or by a
    clinician with an active, unexpired, unrevoked access grant.

    Capability (permission) and context (ownership or consent) are both required.
    """
    if PERM_PATIENT_READ_ANY in permissions:
        return True
    if PERM_PATIENT_READ_OWN in permissions and actor_user_id == patient_owner_user_id:
        return True
    return PERM_PATIENT_READ_WITH_ACCESS in permissions and has_active_access


def is_locked(locked_until: datetime | None, now: datetime) -> bool:
    return locked_until is not None and now < locked_until


def failure_outcome(
    attempts: int, locked_until: datetime | None, now: datetime
) -> tuple[int, datetime | None]:
    """Counter and lock after one failed login.

    A failure after an expired lock starts a new window at one attempt, so a single
    typo later does not re-lock the account. The SQL adapter applies the same rule.
    """
    window_expired = locked_until is not None and locked_until <= now
    current = 0 if window_expired else attempts
    after = current + 1
    if after >= MAX_FAILED_LOGINS:
        return after, now + LOCK_DURATION
    return after, None

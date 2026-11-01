"""Pure authorization rules for the identity module (no I/O)."""

from __future__ import annotations

PERM_PATIENT_READ_OWN = "pacientes.leer_propio"
PERM_PATIENT_READ_WITH_ACCESS = "pacientes.leer_con_acceso"
PERM_PATIENT_READ_ANY = "pacientes.leer_cualquiera"
PERM_USERS_ADMIN = "usuarios.administrar"


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

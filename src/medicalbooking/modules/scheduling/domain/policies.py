"""Scheduling rules that do not depend on infrastructure."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from medicalbooking.shared.domain.errors import ValidationFailed

PERM_BOOK = "citas.reservar"
PERM_CANCEL_MEDICO = "citas.cancelar_medico"
ROLE_PATIENT = "PACIENTE"
# Closed catalog of the reasons a doctor gives when cancelling an appointment of an own agenda.
MOTIVOS_CANCELACION_MEDICO = frozenset({"IMPREVISTO_MEDICO", "OTRO_MEDICO"})
MOTIVO_TEXTO_MAX = 1500  # mirrors citas.motivo_cancelacion_texto nvarchar(1500)
REASON_REQUIRED = "MOTIVO_CANCELACION_REQUERIDO"
REASON_INVALID = "MOTIVO_CANCELACION_INVALIDO"
MAX_WINDOW = timedelta(days=31)
# Appointments that still hold a slot. Mirrors the filtered unique index UX_citas_horario_activa.
ACTIVE_STATES = frozenset({"PENDIENTE_PAGO", "CONFIRMADA"})
# Mirrors the CK_citas_estado check constraint.
APPOINTMENT_STATES = frozenset(
    {"PENDIENTE_PAGO", "CONFIRMADA", "COMPLETADA", "CANCELADA", "EXPIRADA", "NO_ASISTIO"}
)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{8,100}")


def validate_window(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValidationFailed("Dates must include a timezone offset")
    if end <= start:
        raise ValidationFailed("'hasta' must be after 'desde'")
    if end - start > MAX_WINDOW:
        raise ValidationFailed("The window cannot exceed 31 days")


MAX_PAGE_OFFSET = 2_147_483_647  # SQL Server OFFSET takes an INT; larger values fail as a 500


def validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValidationFailed(f"'limit' must be between 1 and {MAX_PAGE_SIZE}")
    if not 0 <= offset <= MAX_PAGE_OFFSET:
        raise ValidationFailed(f"'offset' must be between 0 and {MAX_PAGE_OFFSET}")


def validate_estado(estado: str) -> None:
    if estado not in APPOINTMENT_STATES:
        raise ValidationFailed("'estado' must be one of: " + ", ".join(sorted(APPOINTMENT_STATES)))


def validate_idempotency_key(key: str) -> None:
    if not _KEY_PATTERN.fullmatch(key or ""):
        raise ValidationFailed(
            "Idempotency-Key must be 8-100 characters: letters, digits, '.', '_', ':' or '-'"
        )


def validate_cancellation_reason(codigo: str | None, texto: str | None) -> None:
    """A doctor's cancellation needs a code from the closed set and a non-blank text.

    The text limit counts UTF-16 units, the unit nvarchar(1500) stores, so no accepted text can
    overflow the column.
    """
    if codigo is None or texto is None or not texto.strip():
        raise ValidationFailed(REASON_REQUIRED)
    too_long = len(texto.encode("utf-16-le")) > 2 * MOTIVO_TEXTO_MAX
    if codigo not in MOTIVOS_CANCELACION_MEDICO or too_long:
        raise ValidationFailed(REASON_INVALID)


def is_cancellable(state: str) -> bool:
    return state in ACTIVE_STATES


def is_same_request(
    *,
    existing_patient: str,
    existing_slot: str,
    existing_service: str,
    existing_reason: str | None,
    patient: str,
    slot: str,
    service: str,
    reason: str | None,
) -> bool:
    """A replayed key must repeat the original request exactly, reason included."""
    return (
        existing_patient == patient
        and existing_slot == slot
        and existing_service == service
        and (existing_reason or None) == (reason or None)
    )

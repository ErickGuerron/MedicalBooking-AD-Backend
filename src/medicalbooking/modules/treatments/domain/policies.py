"""Treatment business rules (S4, S5): pure functions, no I/O.

A treatment is a structured entity linked to one appointment. Its free-text plan_tratamiento
in consultas_medicas is a separate summary (S8) and is never read or written here.
"""

from __future__ import annotations

from datetime import date

from medicalbooking.shared.domain.errors import ValidationFailed

ACTIVO = "ACTIVO"
FINALIZADO = "FINALIZADO"
SUSPENDIDO = "SUSPENDIDO"

STATES = frozenset({ACTIVO, FINALIZADO, SUSPENDIDO})
# FINALIZADO is terminal: a closed treatment is never reopened through the API.
_TRANSITIONS: dict[str, frozenset[str]] = {
    ACTIVO: frozenset({FINALIZADO, SUSPENDIDO}),
    SUSPENDIDO: frozenset({ACTIVO, FINALIZADO}),
    FINALIZADO: frozenset(),
}
MAX_DESCRIPCION = 1000
MAX_NOTA = 2000


def validate_estado(estado: str) -> None:
    if estado not in STATES:
        raise ValidationFailed("Unknown treatment state")


def validate_descripcion(descripcion: str) -> None:
    _validate_text(descripcion, MAX_DESCRIPCION, "Treatment description")


def validate_nota(nota: str) -> None:
    _validate_text(nota, MAX_NOTA, "Follow-up note")


def validate_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS[current]:
        raise ValidationFailed("This treatment state change is not allowed")


def validate_fechas(fecha_inicio: date, fecha_fin: date | None, estado: str) -> None:
    if fecha_fin is not None and fecha_fin < fecha_inicio:
        raise ValidationFailed("The end date cannot be before the start date")
    if estado == FINALIZADO and fecha_fin is None:
        raise ValidationFailed("A finalized treatment needs an end date")


def _validate_text(text: str, limit: int, label: str) -> None:
    if not text or not text.strip():
        raise ValidationFailed(f"{label} is required")
    if len(text) > limit:
        raise ValidationFailed(f"{label} is too long")

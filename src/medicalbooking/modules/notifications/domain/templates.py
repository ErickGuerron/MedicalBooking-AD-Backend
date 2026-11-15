"""Patient-facing templates.

Appointment mails stay neutral: they say something changed and where to look. The laboratory
results mail attaches the completed results as a PDF, built at send time and never stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from medicalbooking.modules.synchronization.domain.events import OutboxEvent

CITA_RESERVADA = "CITA_RESERVADA"
CITA_CANCELADA = "CITA_CANCELADA"
RESULTADO_LABORATORIO = "RESULTADO_LABORATORIO"


@dataclass(frozen=True)
class Template:
    subject: str
    text: str


TEMPLATES: dict[str, Template] = {
    CITA_RESERVADA: Template(
        subject="Tienes una actualización en tu cita",
        text=(
            "Hola.\n\n"
            "Tienes una actualización en tu cita. Inicia sesión en Medilo para revisarla.\n"
        ),
    ),
    CITA_CANCELADA: Template(
        subject="Tienes una actualización en tu cita",
        text=(
            "Hola.\n\n"
            "Tienes una actualización en tu cita. Inicia sesión en Medilo para revisarla.\n"
        ),
    ),
    RESULTADO_LABORATORIO: Template(
        subject="Tus resultados de laboratorio están listos",
        text=(
            "Hola.\n\n"
            "Tus resultados de laboratorio ya están listos. Te los enviamos adjuntos en PDF.\n\n"
            "Es un documento confidencial: guárdalo con cuidado. También puedes verlo en Medilo, "
            "en la sección de resultados de tu portal.\n"
        ),
    ),
}

# Only these (aggregate type, event type) pairs reach the patient. Everything else is ignored.
_TEMPLATE_BY_EVENT: dict[tuple[str, str], str] = {
    ("cita", "cita.reservada"): CITA_RESERVADA,
    ("cita", "cita.cancelada"): CITA_CANCELADA,
    ("orden_laboratorio", "resultado_laboratorio.registrado"): RESULTADO_LABORATORIO,
}


def template_for(event: OutboxEvent) -> str | None:
    return _TEMPLATE_BY_EVENT.get((event.aggregate_type, event.event_type))

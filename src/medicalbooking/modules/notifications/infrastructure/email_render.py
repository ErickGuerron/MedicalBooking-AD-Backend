"""Renders the patient emails: the compiled MJML pages, personalised, with a plain-text part.

Values are HTML-escaped for the HTML part only. Ecuador has no daylight saving time, so the
appointment hour is shown at UTC-5 and never converted with the server's local zone.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from string import Template
from typing import Protocol

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    EmailContent,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import (
    CITA_RECORDATORIO,
    RESULTADO_LABORATORIO,
    TEMPLATES,
)

ECUADOR_OFFSET = timedelta(hours=-5)
TEMPLATE_DIR = Path(__file__).parent / "email_templates"
_FALLBACK_NAME = "paciente"


class EmailContext(Protocol):
    def patient_first_name(self, email: str) -> str | None: ...

    def cita_reminder(self, cita_id: str) -> tuple[str, datetime] | None: ...


@cache
def _page(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def results_content(first_name: str, asset_base_url: str) -> EmailContent:
    html_body = _page("resultados.html").substitute(
        first_name=html.escape(first_name),
        asset_base_url=html.escape(asset_base_url.rstrip("/"), quote=True),
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Tus resultados de laboratorio ya están listos. Te los enviamos adjuntos en PDF.\n\n"
        "Es un documento confidencial: guárdalo con cuidado. También puedes verlo en Medilo, "
        "en la sección de resultados de tu portal.\n"
    )
    return EmailContent(
        subject=TEMPLATES[RESULTADO_LABORATORIO].subject, text=text_body, html=html_body
    )


def reminder_content(first_name: str, medico: str, hora: str, asset_base_url: str) -> EmailContent:
    html_body = _page("recordatorio_cita.html").substitute(
        first_name=html.escape(first_name),
        medico_nombre=html.escape(medico),
        hora=html.escape(hora),
        asset_base_url=html.escape(asset_base_url.rstrip("/"), quote=True),
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Hoy tienes una cita con:\n"
        f"{medico}\n"
        f"Hora: {hora}\n\n"
        "Si no puedes asistir, entra a Medilo para revisar tu cita.\n"
    )
    return EmailContent(
        subject=TEMPLATES[CITA_RECORDATORIO].subject, text=text_body, html=html_body
    )


def content_for(
    notification: DueNotification, store: EmailContext, asset_base_url: str
) -> EmailContent:
    """The message of one notification: the patient's name, and the visit for a reminder."""
    first_name = store.patient_first_name(notification.destinatario) or _FALLBACK_NAME
    if notification.tipo == RESULTADO_LABORATORIO:
        return results_content(first_name, asset_base_url)
    if notification.tipo == CITA_RECORDATORIO:
        details = store.cita_reminder(notification.aggregate_id or "")
        if details is None:
            raise NotificationError("APPOINTMENT_NOT_FOUND")
        medico, inicio_utc = details
        hora = (inicio_utc + ECUADOR_OFFSET).strftime("%H:%M")
        return reminder_content(first_name, medico, hora, asset_base_url)
    template = TEMPLATES[notification.tipo]
    return EmailContent(subject=template.subject, text=template.text, html=template.html)

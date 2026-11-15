"""Renders the patient emails: the compiled MJML pages, personalised, with a plain-text part.

Values are HTML-escaped for the HTML part only. Ecuador has no daylight saving time, so the
appointment hour is shown at UTC-5 and never converted with the server's local zone.
"""

from __future__ import annotations

import html
from collections.abc import Callable
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
    CITA_CANCELADA,
    CITA_RECORDATORIO,
    CITA_RESERVADA,
    PAGO_COMPLETADO,
    RESULTADO_LABORATORIO,
    TEMPLATES,
)

ECUADOR_OFFSET = timedelta(hours=-5)
TEMPLATE_DIR = Path(__file__).parent / "email_templates"
_FALLBACK_NAME = "paciente"


class EmailContext(Protocol):
    def patient_first_name(self, email: str) -> str | None: ...

    def cita_reminder(self, cita_id: str) -> tuple[str, datetime] | None: ...

    def paid_invoice(self, pago_id: str) -> tuple[str, str] | None: ...


@cache
def _page(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def _base(asset_base_url: str) -> str:
    return html.escape(asset_base_url.rstrip("/"), quote=True)


def results_content(first_name: str, asset_base_url: str) -> EmailContent:
    html_body = _page("resultados.html").substitute(
        first_name=html.escape(first_name),
        asset_base_url=_base(asset_base_url),
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
        asset_base_url=_base(asset_base_url),
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


def cancellation_content(
    first_name: str, medico: str, fecha: str, hora: str, asset_base_url: str
) -> EmailContent:
    """Appointment cancelled: names the visit so the patient knows exactly which one."""
    html_body = _page("cita_cancelada.html").substitute(
        first_name=html.escape(first_name),
        medico_nombre=html.escape(medico),
        fecha=html.escape(fecha),
        hora=html.escape(hora),
        asset_base_url=_base(asset_base_url),
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Te avisamos que esta cita fue cancelada:\n"
        f"Médico: {medico}\n"
        f"Fecha: {fecha}\n"
        f"Hora: {hora}\n\n"
        "Si quieres agendar otra cita, entra a Medilo en cualquier momento.\n"
    )
    return EmailContent(subject=TEMPLATES[CITA_CANCELADA].subject, text=text_body, html=html_body)


def reservation_content(
    first_name: str, medico: str, fecha: str, hora: str, asset_base_url: str
) -> EmailContent:
    """Appointment booked: confirms the visit with the doctor, date and hour."""
    html_body = _page("cita_reservada.html").substitute(
        first_name=html.escape(first_name),
        medico_nombre=html.escape(medico),
        fecha=html.escape(fecha),
        hora=html.escape(hora),
        asset_base_url=_base(asset_base_url),
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Tu cita está reservada:\n"
        f"Médico: {medico}\n"
        f"Fecha: {fecha}\n"
        f"Hora: {hora}\n\n"
        "Si necesitas cambiar algo, entra a Medilo con anticipación.\n"
    )
    return EmailContent(subject=TEMPLATES[CITA_RESERVADA].subject, text=text_body, html=html_body)


def payment_content(
    first_name: str, invoice_number: str, total: str, asset_base_url: str, download_url: str
) -> EmailContent:
    """Payment confirmed: greets the patient, attaches the invoice and links a signed PDF."""
    html_body = _page("pago_completado.html").substitute(
        first_name=html.escape(first_name),
        invoice_number=html.escape(invoice_number),
        total=html.escape(total),
        asset_base_url=_base(asset_base_url),
        download_url=html.escape(download_url, quote=True),
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Recibimos tu pago. Te enviamos tu factura adjunta en PDF.\n\n"
        f"Factura: {invoice_number}\nTotal: {total}\n\n"
        f"También puedes descargarla aquí (el enlace caduca en 7 días):\n{download_url}\n"
    )
    return EmailContent(subject=TEMPLATES[PAGO_COMPLETADO].subject, text=text_body, html=html_body)


def _visit(store: EmailContext, aggregate_id: str | None) -> tuple[str, str, str]:
    """Doctor, date and hour of the appointment, with the hour shown in Ecuador time."""
    details = store.cita_reminder(aggregate_id or "")
    if details is None:
        raise NotificationError("APPOINTMENT_NOT_FOUND")
    medico, inicio_utc = details
    local = inicio_utc + ECUADOR_OFFSET
    return medico, local.strftime("%d/%m/%Y"), local.strftime("%H:%M")


def content_for(
    notification: DueNotification,
    store: EmailContext,
    asset_base_url: str,
    invoice_download_url: Callable[[str], str] | None = None,
) -> EmailContent:
    """The message of one notification: the patient's name, and the visit for appointments.

    `invoice_download_url` maps a payment id to its signed PDF link. Payment emails need it.
    """
    first_name = store.patient_first_name(notification.destinatario) or _FALLBACK_NAME
    if notification.tipo == RESULTADO_LABORATORIO:
        return results_content(first_name, asset_base_url)
    if notification.tipo == CITA_CANCELADA:
        medico, fecha, hora = _visit(store, notification.aggregate_id)
        return cancellation_content(first_name, medico, fecha, hora, asset_base_url)
    if notification.tipo == CITA_RESERVADA:
        medico, fecha, hora = _visit(store, notification.aggregate_id)
        return reservation_content(first_name, medico, fecha, hora, asset_base_url)
    if notification.tipo == PAGO_COMPLETADO:
        receipt = store.paid_invoice(notification.aggregate_id or "")
        if receipt is None:
            raise NotificationError("INVOICE_NOT_FOUND")
        if invoice_download_url is None:
            raise NotificationError("INVOICE_LINK_NOT_CONFIGURED")
        number, total = receipt
        download_url = invoice_download_url(notification.aggregate_id or "")
        return payment_content(first_name, number, total, asset_base_url, download_url)
    if notification.tipo == CITA_RECORDATORIO:
        medico, _, hora = _visit(store, notification.aggregate_id)
        return reminder_content(first_name, medico, hora, asset_base_url)
    template = TEMPLATES[notification.tipo]
    return EmailContent(subject=template.subject, text=template.text, html=template.html)

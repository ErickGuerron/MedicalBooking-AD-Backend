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
    CitaCancellation,
    DueNotification,
    EmailContent,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import (
    CITA_CANCELADA,
    CITA_CANCELADA_MEDICO,
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

    def cita_patient_name(self, cita_id: str) -> str | None: ...

    def cita_cancellation(self, cita_id: str) -> CitaCancellation | None: ...

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


MOTIVO_LABELS = {
    "IMPREVISTO_MEDICO": "Imprevisto del médico",
    "OTRO_MEDICO": "Otro motivo",
}
SIN_MOTIVO_PACIENTE = "El paciente no indicó un motivo."


def motivo_text(cancellation: CitaCancellation) -> str | None:
    """The stored reason as one line: the code's label, then the free text. None when absent."""
    label = MOTIVO_LABELS.get(cancellation.motivo_codigo or "")
    texto = (cancellation.motivo_texto or "").strip()
    if label and texto:
        return f"{label}: {texto}"
    return label or texto or None


def _motivo_html(motivo: str | None) -> str:
    if motivo is None:
        return ""
    return (
        '<div style="font-size:13.5px;color:#4A5280;margin-top:12px;">Motivo</div>'
        '<div style="font-size:17px;font-weight:700;color:#16206B;">'
        f"{html.escape(motivo)}</div>"
    )


def cancellation_content(
    first_name: str,
    medico: str,
    fecha: str,
    hora: str,
    asset_base_url: str,
    *,
    por_medico: bool = False,
    motivo: str | None = None,
) -> EmailContent:
    """Appointment cancelled. If the doctor cancelled it, the mail says so and gives the reason."""
    if por_medico:
        subject = "El médico canceló tu cita en Medilo"
        titulo = "Tu médico canceló<br>tu cita."
        preheader = f"{medico} canceló tu cita del {fecha} a las {hora}."
        detalle = "Esta cita ya no está programada. Puedes agendar otra cuando quieras."
        intro = "Tu médico canceló esta cita:"
    else:
        subject = TEMPLATES[CITA_CANCELADA].subject
        titulo = "Tu cita fue<br>cancelada."
        preheader = f"Tu cita con {medico} del {fecha} a las {hora} fue cancelada."
        detalle = "Esta cita ya no está programada y no tienes que hacer nada más."
        intro = "Te avisamos que esta cita fue cancelada:"
    html_body = _page("cita_cancelada.html").substitute(
        first_name=html.escape(first_name),
        preheader=html.escape(preheader),
        titulo=titulo,
        detalle=html.escape(detalle),
        medico_nombre=html.escape(medico),
        fecha=html.escape(fecha),
        hora=html.escape(hora),
        motivo_bloque=_motivo_html(motivo),
        asset_base_url=_base(asset_base_url),
    )
    motivo_line = f"Motivo: {motivo}\n" if motivo else ""
    text_body = (
        f"Hola {first_name}.\n\n"
        f"{intro}\n"
        f"Médico: {medico}\n"
        f"Fecha: {fecha}\n"
        f"Hora: {hora}\n"
        f"{motivo_line}\n"
        "Si quieres agendar otra cita, entra a Medilo en cualquier momento.\n"
    )
    return EmailContent(subject=subject, text=text_body, html=html_body)


def doctor_cancellation_content(
    doctor_first_name: str,
    paciente: str,
    fecha: str,
    hora: str,
    *,
    por_medico: bool = False,
    motivo: str | None = None,
) -> EmailContent:
    """Appointment cancelled, addressed to the doctor: the patient, the slot, who cancelled, why."""
    if por_medico:
        subject = "Cancelaste una cita en Medilo"
        intro = "Cancelaste esta cita:"
        sin_motivo = "no indicado"
    else:
        subject = TEMPLATES[CITA_CANCELADA_MEDICO].subject
        intro = "El paciente canceló esta cita:"
        sin_motivo = SIN_MOTIVO_PACIENTE
    text_body = (
        f"Hola {doctor_first_name}.\n\n"
        f"{intro}\n"
        f"Paciente: {paciente}\n"
        f"Fecha: {fecha}\n"
        f"Hora: {hora}\n"
        f"Motivo: {motivo or sin_motivo}\n\n"
        "Entra a Medilo para ver los detalles de tu agenda.\n"
    )
    return EmailContent(subject=subject, text=text_body)
    return EmailContent(subject=TEMPLATES[CITA_CANCELADA_MEDICO].subject, text=text_body)


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


def _cancellation(store: EmailContext, aggregate_id: str | None) -> CitaCancellation:
    """Who cancelled the appointment and the reason stored with it."""
    cancellation = store.cita_cancellation(aggregate_id or "")
    if cancellation is None:
        raise NotificationError("APPOINTMENT_NOT_FOUND")
    return cancellation


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
        cancellation = _cancellation(store, notification.aggregate_id)
        return cancellation_content(
            first_name,
            medico,
            fecha,
            hora,
            asset_base_url,
            por_medico=cancellation.por_medico,
            motivo=motivo_text(cancellation),
        )
    if notification.tipo == CITA_CANCELADA_MEDICO:
        _, fecha, hora = _visit(store, notification.aggregate_id)
        cancellation = _cancellation(store, notification.aggregate_id)
        paciente = store.cita_patient_name(notification.aggregate_id or "")
        if paciente is None:
            raise NotificationError("APPOINTMENT_NOT_FOUND")
        return doctor_cancellation_content(
            first_name,
            paciente,
            fecha,
            hora,
            por_medico=cancellation.por_medico,
            motivo=motivo_text(cancellation),
        )
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

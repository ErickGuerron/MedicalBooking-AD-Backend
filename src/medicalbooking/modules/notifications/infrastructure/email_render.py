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
    CITA_CANCELADA,
    CITA_RECORDATORIO,
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


def cancellation_content(
    first_name: str, medico: str, fecha: str, hora: str, asset_base_url: str
) -> EmailContent:
    """Appointment cancelled: names the visit so the patient knows exactly which one."""
    name = html.escape(first_name)
    doctor = html.escape(medico)
    day = html.escape(fecha)
    time_of_day = html.escape(hora)
    base = html.escape(asset_base_url.rstrip("/"), quote=True)
    html_body = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#fff4ea;font-family:Arial,Helvetica,'
        'sans-serif;color:#1f2937;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#fff4ea;padding:24px 12px;"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;width:100%;background:#ffffff;border-radius:16px;'
        'overflow:hidden;">'
        '<tr><td style="background:#c2410c;padding:24px 28px;color:#ffffff;">'
        '<div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;'
        'opacity:.9;">Medilo · Citas</div>'
        '<div style="font-size:24px;font-weight:700;margin-top:6px;">Tu cita fue cancelada'
        "</div></td></tr>"
        '<tr><td style="padding:28px;">'
        '<p style="margin:0 0 16px;font-size:16px;line-height:1.5;">Hola '
        f"{name}.</p>"
        '<p style="margin:0 0 20px;font-size:16px;line-height:1.5;">Te avisamos que '
        "esta cita fue cancelada:</p>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#fff4ea;border-radius:12px;"><tr><td style="padding:16px 18px;'
        'font-size:15px;line-height:1.7;">'
        f'<strong style="color:#9a3412;">Médico:</strong> {doctor}<br>'
        f'<strong style="color:#9a3412;">Fecha:</strong> {day}<br>'
        f'<strong style="color:#9a3412;">Hora:</strong> {time_of_day}'
        "</td></tr></table>"
        '<p style="margin:20px 0 0;font-size:14px;line-height:1.5;color:#475569;">Si quieres '
        "agendar otra cita, entra a Medilo en cualquier momento.</p>"
        f'<p style="margin:20px 0 0;"><a href="{base}" style="color:#c2410c;">Ir a Medilo</a></p>'
        "</td></tr>"
        '<tr><td style="padding:18px 28px;background:#f8fafc;font-size:12px;line-height:1.5;'
        'color:#475569;">Medilo · Este mensaje es automatico, no lo respondas.</td></tr>'
        "</table></td></tr></table></body></html>"
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


def payment_content(
    first_name: str, invoice_number: str, total: str, asset_base_url: str
) -> EmailContent:
    """Payment confirmed: greets the patient and names the invoice attached as a PDF."""
    name = html.escape(first_name)
    number = html.escape(invoice_number)
    amount = html.escape(total)
    html_body = (
        '<!doctype html><html lang="es"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:24px;background:#fff4ea;font-family:Arial,Helvetica,'
        'sans-serif;color:#1f2937;">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;'
        'padding:28px;">'
        '<div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;'
        'color:#c2410c;">Medilo · Pagos</div>'
        '<p style="font-size:16px;line-height:1.5;">Hola '
        f"{name}.</p>"
        '<p style="font-size:16px;line-height:1.5;">Recibimos tu pago. Te enviamos tu factura '
        "adjunta en PDF.</p>"
        '<table role="presentation" width="100%" style="background:#fff4ea;border-radius:12px;">'
        '<tr><td style="padding:16px 18px;font-size:15px;line-height:1.6;">'
        f"<strong>Factura</strong> {number}<br><strong>Total</strong> {amount}"
        "</td></tr></table>"
        '<p style="font-size:12px;color:#475569;margin-top:20px;">Medilo · Este mensaje es '
        "automatico, no lo respondas.</p></div></body></html>"
    )
    text_body = (
        f"Hola {first_name}.\n\n"
        "Recibimos tu pago. Te enviamos tu factura adjunta en PDF.\n\n"
        f"Factura: {invoice_number}\nTotal: {total}\n"
    )
    return EmailContent(subject=TEMPLATES[PAGO_COMPLETADO].subject, text=text_body, html=html_body)


def content_for(
    notification: DueNotification, store: EmailContext, asset_base_url: str
) -> EmailContent:
    """The message of one notification: the patient's name, and the visit for a reminder."""
    first_name = store.patient_first_name(notification.destinatario) or _FALLBACK_NAME
    if notification.tipo == RESULTADO_LABORATORIO:
        return results_content(first_name, asset_base_url)
    if notification.tipo == CITA_CANCELADA:
        details = store.cita_reminder(notification.aggregate_id or "")
        if details is None:
            raise NotificationError("APPOINTMENT_NOT_FOUND")
        medico, inicio_utc = details
        local = inicio_utc + ECUADOR_OFFSET
        return cancellation_content(
            first_name,
            medico,
            local.strftime("%d/%m/%Y"),
            local.strftime("%H:%M"),
            asset_base_url,
        )
    if notification.tipo == PAGO_COMPLETADO:
        receipt = store.paid_invoice(notification.aggregate_id or "")
        if receipt is None:
            raise NotificationError("INVOICE_NOT_FOUND")
        number, total = receipt
        return payment_content(first_name, number, total, asset_base_url)
    if notification.tipo == CITA_RECORDATORIO:
        details = store.cita_reminder(notification.aggregate_id or "")
        if details is None:
            raise NotificationError("APPOINTMENT_NOT_FOUND")
        medico, inicio_utc = details
        hora = (inicio_utc + ECUADOR_OFFSET).strftime("%H:%M")
        return reminder_content(first_name, medico, hora, asset_base_url)
    template = TEMPLATES[notification.tipo]
    return EmailContent(subject=template.subject, text=template.text, html=template.html)

# ruff: noqa: E501  (the email markup and copy are long on purpose; they are static strings)
"""Patient-facing templates.

Appointment and payment mails are designed and personalised (name, visit, invoice). The
laboratory results mail and the invoice attach their PDF, built at send time and never stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from medicalbooking.modules.synchronization.domain.events import OutboxEvent

CITA_RESERVADA = "CITA_RESERVADA"
CITA_CANCELADA = "CITA_CANCELADA"
# The doctor's copy of a cancelled appointment: addressed to the doctor, names the patient.
CITA_CANCELADA_MEDICO = "CITA_CANCELADA_MEDICO"
RESULTADO_LABORATORIO = "RESULTADO_LABORATORIO"
CITA_RECORDATORIO = "CITA_RECORDATORIO"
PAGO_COMPLETADO = "PAGO_COMPLETADO"


@dataclass(frozen=True)
class Template:
    subject: str
    text: str
    # Optional HTML body; the text stays as the plain-text part for clients that cannot render it.
    html: str | None = None


# Static markup: no patient data goes into it, so nothing needs escaping.
RESULTADO_HTML = """<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#fff4ea;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff4ea;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;">
        <tr><td style="background:#c2410c;padding:24px 28px;color:#ffffff;">
          <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.9;">Medilo · Laboratorio</div>
          <div style="font-size:24px;font-weight:700;margin-top:6px;">Tus resultados están listos</div>
        </td></tr>
        <tr><td style="padding:28px;">
          <p style="margin:0 0 16px;font-size:16px;line-height:1.5;">Hola.</p>
          <p style="margin:0 0 20px;font-size:16px;line-height:1.5;">
            Tus resultados de laboratorio ya están disponibles. Los enviamos adjuntos en PDF.
          </p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff4ea;border-radius:12px;">
            <tr><td style="padding:16px 18px;font-size:15px;line-height:1.5;color:#1f2937;">
              <strong style="color:#9a3412;">Tu PDF de resultados</strong><br>
              Ábrelo desde los adjuntos de este correo. También puedes verlo en la sección de
              resultados de tu portal de Medilo.
            </td></tr>
          </table>
          <p style="margin:20px 0 0;font-size:14px;line-height:1.5;color:#475569;">
            Es un documento confidencial: guárdalo con cuidado y no lo compartas con terceros.
          </p>
        </td></tr>
        <tr><td style="padding:18px 28px;background:#f8fafc;font-size:12px;line-height:1.5;color:#475569;">
          Medilo · Este mensaje es automático, no lo respondas.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

TEMPLATES: dict[str, Template] = {
    CITA_RESERVADA: Template(
        subject="Tu cita en Medilo está reservada",
        text=(
            "Hola.\n\n"
            "Tienes una actualización en tu cita. Inicia sesión en Medilo para revisarla.\n"
        ),
    ),
    CITA_CANCELADA: Template(
        subject="Tu cita en Medilo fue cancelada",
        text=(
            "Hola.\n\n"
            "Te avisamos que una de tus citas fue cancelada. Entra a Medilo para ver los detalles.\n"
        ),
    ),
    CITA_CANCELADA_MEDICO: Template(
        subject="Se canceló una cita en Medilo",
        text=("Hola.\n\nSe canceló una de tus citas. Entra a Medilo para ver los detalles.\n"),
    ),
    RESULTADO_LABORATORIO: Template(
        subject="Tus resultados de laboratorio están listos",
        text=(
            "Hola.\n\n"
            "Tus resultados de laboratorio ya están listos. Te los enviamos adjuntos en PDF.\n\n"
            "Es un documento confidencial: guárdalo con cuidado. También puedes verlo en Medilo, "
            "en la sección de resultados de tu portal.\n"
        ),
        html=RESULTADO_HTML,
    ),
    CITA_RECORDATORIO: Template(
        subject="Hoy tienes una cita en Medilo",
        text="Hola.\n\nHoy tienes una cita médica. Inicia sesión en Medilo para revisarla.\n",
    ),
    PAGO_COMPLETADO: Template(
        subject="Recibimos tu pago en Medilo",
        text=(
            "Hola.\n\n"
            "Recibimos tu pago. Te enviamos tu factura adjunta en PDF.\n\n"
            "Puedes revisarla también en Medilo, en la sección de pagos de tu portal.\n"
        ),
    ),
}

# Only these (aggregate type, event type) pairs notify. Everything else is ignored. A cancelled
# appointment notifies both its patient and its doctor, each with their own template.
_TEMPLATES_BY_EVENT: dict[tuple[str, str], tuple[str, ...]] = {
    ("cita", "cita.reservada"): (CITA_RESERVADA,),
    ("cita", "cita.cancelada"): (CITA_CANCELADA, CITA_CANCELADA_MEDICO),
    ("orden_laboratorio", "orden_laboratorio.confirmada"): (RESULTADO_LABORATORIO,),
    ("cita", "cita.recordatorio"): (CITA_RECORDATORIO,),
    ("pago", "pago.actualizado"): (PAGO_COMPLETADO,),
}

# Templates addressed to the doctor of the appointment. The rest go to the patient.
DOCTOR_TEMPLATES = frozenset({CITA_CANCELADA_MEDICO})


def templates_for(event: OutboxEvent) -> tuple[str, ...]:
    return _TEMPLATES_BY_EVENT.get((event.aggregate_type, event.event_type), ())

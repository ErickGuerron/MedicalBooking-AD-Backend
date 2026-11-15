from __future__ import annotations

from datetime import UTC, datetime

from medicalbooking.modules.notifications.application.ports import DueNotification
from medicalbooking.modules.notifications.domain.templates import CITA_CANCELADA
from medicalbooking.modules.notifications.infrastructure.email_render import (
    cancellation_content,
    content_for,
)

APPOINTMENT_START = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)  # 09:30 in Ecuador (UTC-5)


class _Store:
    def __init__(self, reminder=("Dr. Luis Perez", APPOINTMENT_START)) -> None:
        self._reminder = reminder

    def patient_first_name(self, email: str) -> str | None:
        return "Ana"

    def cita_reminder(self, cita_id: str):
        return self._reminder

    def paid_invoice(self, pago_id: str):
        return None


def test_cancellation_email_names_the_visit_that_was_cancelled() -> None:
    content = cancellation_content("Ana", "Dr. Luis Perez", "18/11/2026", "09:30", "https://a.ex")

    assert content.subject == "Tu cita en Medilo fue cancelada"
    assert "Hola Ana" in content.text
    assert "Dr. Luis Perez" in content.text
    assert "18/11/2026" in content.text
    assert "09:30" in content.text
    assert content.html is not None
    assert "Dr. Luis Perez" in content.html
    assert "09:30" in content.html


def test_cancellation_email_escapes_html_in_patient_and_doctor_names() -> None:
    content = cancellation_content(
        "<b>Ana</b>", "<img src=x>", "18/11/2026", "09:30", "https://a.ex"
    )

    assert content.html is not None
    assert "<img src=x>" not in content.html
    assert "<b>Ana</b>" not in content.html


def test_cancellation_notification_uses_the_visit_date_and_hour_in_ecuador() -> None:
    notification = DueNotification(
        id="11111111-1111-1111-1111-111111111111",
        tipo=CITA_CANCELADA,
        destinatario="ana@example.com",
        numero_intentos=0,
        aggregate_id="22222222-2222-2222-2222-222222222222",
    )

    content = content_for(notification, _Store(), "https://a.ex")

    assert content.subject == "Tu cita en Medilo fue cancelada"
    assert "18/11/2026" in content.text
    assert "09:30" in content.text
    assert "Dr. Luis Perez" in content.text

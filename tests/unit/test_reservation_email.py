from __future__ import annotations

from datetime import UTC, datetime

import pytest

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import CITA_RESERVADA
from medicalbooking.modules.notifications.infrastructure.email_render import (
    content_for,
    reservation_content,
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


def _due(aggregate_id: str | None) -> DueNotification:
    return DueNotification(
        id="33333333-3333-3333-3333-333333333333",
        tipo=CITA_RESERVADA,
        destinatario="ana@example.com",
        numero_intentos=0,
        aggregate_id=aggregate_id,
    )


def test_reservation_email_names_the_doctor_date_and_hour() -> None:
    content = reservation_content("Ana", "Dr. Luis Perez", "18/11/2026", "09:30", "https://a.ex")

    assert content.subject == "Tu cita en Medilo está reservada"
    assert "Hola Ana" in content.text
    assert "Dr. Luis Perez" in content.text
    assert "18/11/2026" in content.text
    assert "09:30" in content.text
    assert content.html is not None
    assert "Dr. Luis Perez" in content.html
    assert "18/11/2026" in content.html
    assert "$medico_nombre" not in content.html and "$fecha" not in content.html


def test_reservation_email_escapes_html_in_the_doctor_name() -> None:
    content = reservation_content("Ana", "<img src=x>", "18/11/2026", "09:30", "https://a.ex")

    assert content.html is not None
    assert "<img src=x>" not in content.html


def test_reservation_notification_is_built_from_the_appointment_in_ecuador_time() -> None:
    content = content_for(_due("22222222-2222-2222-2222-222222222222"), _Store(), "https://a.ex")

    assert content.subject == "Tu cita en Medilo está reservada"
    assert "18/11/2026" in content.text
    assert "09:30" in content.text
    assert "Dr. Luis Perez" in content.text


def test_a_reservation_for_a_missing_appointment_is_refused() -> None:
    with pytest.raises(NotificationError) as error:
        content_for(_due("gone"), _Store(reminder=None), "https://a.ex")

    assert error.value.code == "APPOINTMENT_NOT_FOUND"

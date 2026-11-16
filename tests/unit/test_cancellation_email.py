from __future__ import annotations

from datetime import UTC, datetime

import pytest

from medicalbooking.modules.notifications.application.ports import (
    CitaCancellation,
    DueNotification,
)
from medicalbooking.modules.notifications.domain.templates import (
    CITA_CANCELADA,
    CITA_CANCELADA_MEDICO,
)
from medicalbooking.modules.notifications.infrastructure.email_render import (
    cancellation_content,
    content_for,
    motivo_text,
)

APPOINTMENT_START = datetime(2026, 11, 18, 14, 30, tzinfo=UTC)  # 09:30 in Ecuador (UTC-5)


class _Store:
    def __init__(
        self,
        reminder=("Dr. Luis Perez", APPOINTMENT_START),
        cancellation: CitaCancellation | None = None,
    ) -> None:
        self._reminder = reminder
        self._cancellation = cancellation or CitaCancellation(por_medico=False)

    def patient_first_name(self, email: str) -> str | None:
        return "Ana"

    def cita_reminder(self, cita_id: str):
        return self._reminder

    def paid_invoice(self, pago_id: str):
        return None

    def cita_patient_name(self, cita_id: str) -> str | None:
        return "Ana Torres"

    def cita_cancellation(self, cita_id: str) -> CitaCancellation | None:
        return self._cancellation


def _notification(tipo: str, destinatario: str) -> DueNotification:
    return DueNotification(
        id="11111111-1111-1111-1111-111111111111",
        tipo=tipo,
        destinatario=destinatario,
        numero_intentos=0,
        aggregate_id="22222222-2222-2222-2222-222222222222",
    )


def test_patient_is_told_the_doctor_cancelled_and_why() -> None:
    store = _Store(
        cancellation=CitaCancellation(
            por_medico=True,
            motivo_codigo="IMPREVISTO_MEDICO",
            motivo_texto="Emergencia en el hospital",
        )
    )

    content = content_for(_notification(CITA_CANCELADA, "ana@example.com"), store, "https://a.ex")

    assert content.subject == "El médico canceló tu cita en Medilo"
    assert "Tu médico canceló esta cita" in content.text
    assert "Motivo: Imprevisto del médico: Emergencia en el hospital" in content.text
    assert content.html is not None
    assert "Tu médico canceló" in content.html
    assert "Imprevisto del médico: Emergencia en el hospital" in content.html


def test_patient_cancellation_keeps_the_patient_copy_without_a_reason() -> None:
    content = content_for(
        _notification(CITA_CANCELADA, "ana@example.com"), _Store(), "https://a.ex"
    )

    assert content.subject == "Tu cita en Medilo fue cancelada"
    assert "Motivo" not in content.text
    assert content.html is not None
    assert "Tu cita fue" in content.html
    assert ">Motivo<" not in content.html


def test_doctor_copy_says_the_patient_cancelled_with_no_reason_on_file() -> None:
    content = content_for(
        _notification(CITA_CANCELADA_MEDICO, "luis@example.com"), _Store(), "https://a.ex"
    )

    assert content.subject == "Se canceló una cita en Medilo"
    assert "El paciente canceló esta cita" in content.text
    assert "Motivo: El paciente no indicó un motivo." in content.text


def test_doctor_copy_shows_the_reason_the_patient_wrote() -> None:
    store = _Store(
        cancellation=CitaCancellation(por_medico=False, motivo_texto="Me surgió un viaje")
    )

    content = content_for(
        _notification(CITA_CANCELADA_MEDICO, "luis@example.com"), store, "https://a.ex"
    )

    assert "El paciente canceló esta cita" in content.text
    assert "Motivo: Me surgió un viaje" in content.text
    assert "no indicó un motivo" not in content.text


def test_doctor_copy_confirms_the_doctor_own_cancellation_with_the_reason() -> None:
    store = _Store(
        cancellation=CitaCancellation(
            por_medico=True, motivo_codigo="OTRO_MEDICO", motivo_texto="Congreso"
        )
    )

    content = content_for(
        _notification(CITA_CANCELADA_MEDICO, "luis@example.com"), store, "https://a.ex"
    )

    assert content.subject == "Cancelaste una cita en Medilo"
    assert "Cancelaste esta cita" in content.text
    assert "Motivo: Otro motivo: Congreso" in content.text


def test_stored_reason_escapes_html_in_the_patient_mail() -> None:
    store = _Store(
        cancellation=CitaCancellation(
            por_medico=True, motivo_codigo="OTRO_MEDICO", motivo_texto="<script>x</script>"
        )
    )

    content = content_for(_notification(CITA_CANCELADA, "ana@example.com"), store, "https://a.ex")

    assert content.html is not None
    assert "<script>x</script>" not in content.html
    assert "&lt;script&gt;x&lt;/script&gt;" in content.html


@pytest.mark.parametrize(
    ("codigo", "texto", "expected"),
    [
        ("IMPREVISTO_MEDICO", "Texto", "Imprevisto del médico: Texto"),
        ("OTRO_MEDICO", None, "Otro motivo"),
        (None, "Solo texto", "Solo texto"),
        (None, None, None),
        ("OTRO_MEDICO", "   ", "Otro motivo"),
    ],
)
def test_motivo_text_combines_the_label_and_the_free_text(codigo, texto, expected) -> None:
    assert motivo_text(CitaCancellation(True, codigo, texto)) == expected


def test_doctor_copy_is_addressed_to_the_doctor_and_names_the_patient() -> None:
    notification = DueNotification(
        id="11111111-1111-1111-1111-111111111111",
        tipo=CITA_CANCELADA_MEDICO,
        destinatario="luis@example.com",
        numero_intentos=0,
        aggregate_id="22222222-2222-2222-2222-222222222222",
    )

    content = content_for(notification, _Store(), "https://a.ex")

    assert content.subject == "Se canceló una cita en Medilo"
    assert content.text.startswith("Hola Ana.")
    assert "Paciente: Ana Torres" in content.text
    assert "18/11/2026" in content.text
    assert "09:30" in content.text
    assert content.html is None


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

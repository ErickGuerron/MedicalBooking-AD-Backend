"""Patient emails: the greeting with the name, the reminder's details, and the Ecuador day window.

Pure rendering and time rules; the queueing of reminders is pinned by the integration suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import (
    CITA_RECORDATORIO,
    RESULTADO_LABORATORIO,
)
from medicalbooking.modules.notifications.infrastructure.email_render import (
    content_for,
    reminder_content,
    results_content,
)
from medicalbooking.workers.appointment_reminders import ecuador_day_bounds

BASE = "https://assets.example.test"


class FakeStore:
    def __init__(self, name: str | None = "Ana", cita=None) -> None:
        self._name = name
        self._cita = cita

    def patient_first_name(self, email: str) -> str | None:
        return self._name

    def cita_reminder(self, cita_id: str):
        return self._cita


def _due(tipo: str, aggregate: str | None = None) -> DueNotification:
    return DueNotification(
        id="n1",
        tipo=tipo,
        destinatario="ana@example.test",
        numero_intentos=0,
        aggregate_id=aggregate,
    )


def test_the_results_mail_greets_the_patient_by_name() -> None:
    content = results_content("Ana", BASE)

    assert content.text.startswith("Hola Ana.")
    assert "Hola Ana," in content.html
    assert "$first_name" not in content.html


def test_the_name_is_escaped_in_the_html_only() -> None:
    content = results_content("<b>Ana</b>", BASE)

    assert "&lt;b&gt;Ana&lt;/b&gt;" in content.html
    assert "<b>Ana</b>" in content.text


def test_the_reminder_names_the_doctor_and_the_hour() -> None:
    content = reminder_content("Ana", "Dra. Lucía Paz", "08:30", BASE)

    assert "Hoy tienes una cita con:" in content.text
    assert "Dra. Lucía Paz" in content.text
    assert "Hora: 08:30" in content.text
    assert "Dra. Lucía Paz" in content.html
    assert "$medico_nombre" not in content.html and "$hora" not in content.html


def test_the_reminder_is_built_from_the_appointment_at_ecuador_time() -> None:
    inicio_utc = datetime(2026, 11, 15, 13, 30)  # 08:30 in Ecuador (UTC-5)

    content = content_for(
        _due(CITA_RECORDATORIO, aggregate="cita-1"),
        FakeStore(cita=("Dra. Lucía Paz", inicio_utc)),
        BASE,
    )

    assert "Hora: 08:30" in content.text
    assert content.subject == "Hoy tienes una cita en Medilo"


def test_a_reminder_for_a_missing_appointment_is_refused() -> None:
    with pytest.raises(NotificationError) as error:
        content_for(_due(CITA_RECORDATORIO, aggregate="gone"), FakeStore(cita=None), BASE)

    assert error.value.code == "APPOINTMENT_NOT_FOUND"


def test_a_patient_without_a_name_is_greeted_with_the_neutral_word() -> None:
    content = content_for(_due(RESULTADO_LABORATORIO), FakeStore(name=None), BASE)

    assert content.text.startswith("Hola paciente.")


def test_the_ecuador_day_starts_at_five_in_the_morning_utc() -> None:
    start, end = ecuador_day_bounds(datetime(2026, 11, 15, 13, 49, tzinfo=UTC))

    assert start == datetime(2026, 11, 15, 5, 0)
    assert end == datetime(2026, 11, 16, 5, 0)


def test_an_early_utc_hour_still_belongs_to_the_previous_ecuador_day() -> None:
    start, end = ecuador_day_bounds(datetime(2026, 11, 15, 3, 0, tzinfo=UTC))

    assert start == datetime(2026, 11, 14, 5, 0)
    assert end == datetime(2026, 11, 15, 5, 0)

"""Notification sender: retry schedule, FALLIDA after the last attempt, error codes only."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from medicalbooking.modules.notifications.application.dispatcher import NotificationDispatcher
from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    EmailMessage,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.delivery import MAX_ATTEMPTS, next_attempt_at
from medicalbooking.modules.notifications.domain.templates import TEMPLATES

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
NOTIFICATION_ID = "55555555-5555-5555-5555-555555555555"
SECRET = "xkeyn-SECRET-123"


class _Queue:
    def __init__(self, attempts_before: int = 0, tipo: str = "CITA_RESERVADA") -> None:
        self._row = DueNotification(
            id=NOTIFICATION_ID,
            tipo=tipo,
            destinatario="ana@example.com",
            numero_intentos=attempts_before,
        )
        self.sent: list[tuple] = []
        self.failed: list[tuple] = []

    def claim_due(self, now, lease_until, limit):
        return [self._row]

    def mark_sent(self, notification_id, provider_message_id, now):
        self.sent.append((notification_id, provider_message_id))

    def mark_attempt_failed(self, notification_id, attempts, error_code, next_attempt_at, now):
        self.failed.append((notification_id, attempts, error_code, next_attempt_at))


class _Sender:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str | None:
        self.messages.append(message)
        if self._error is not None:
            raise self._error
        return "provider-message-1"


def _run(queue: _Queue, sender: _Sender):
    dispatcher = NotificationDispatcher(queue=queue, sender=sender, clock=lambda: NOW)
    return dispatcher.send_due(limit=10)


def test_successful_send_marks_the_row_sent_with_the_provider_id() -> None:
    queue = _Queue()

    report = _run(queue, _Sender())

    assert queue.sent == [(NOTIFICATION_ID, "provider-message-1")]
    assert queue.failed == []
    assert (report.attempted, report.sent, report.failed) == (1, 1, 0)


def test_message_goes_to_the_recipient_with_the_template_text() -> None:
    sender = _Sender()

    _run(_Queue(tipo="CITA_CANCELADA"), sender)

    message = sender.messages[0]
    assert message.to == "ana@example.com"
    assert message.subject == TEMPLATES["CITA_CANCELADA"].subject
    assert message.text == TEMPLATES["CITA_CANCELADA"].text


@pytest.mark.parametrize(
    ("attempt", "minutes"),
    [(1, 1), (2, 2), (3, 4), (4, 8)],
)
def test_retry_waits_follow_exponential_backoff(attempt: int, minutes: int) -> None:
    assert next_attempt_at(attempt, NOW) == NOW + timedelta(minutes=minutes)


def test_the_last_attempt_has_no_next_attempt() -> None:
    assert MAX_ATTEMPTS == 5
    assert next_attempt_at(MAX_ATTEMPTS, NOW) is None


def test_first_failure_schedules_a_retry_in_one_minute() -> None:
    queue = _Queue(attempts_before=0)

    _run(queue, _Sender(error=NotificationError("PROVIDER_UNAVAILABLE")))

    notification_id, attempts, code, next_at = queue.failed[0]
    assert (notification_id, attempts, code) == (NOTIFICATION_ID, 1, "PROVIDER_UNAVAILABLE")
    assert next_at == NOW + timedelta(minutes=1)


def test_fifth_failed_attempt_marks_the_row_fallida() -> None:
    queue = _Queue(attempts_before=MAX_ATTEMPTS - 1)

    _run(queue, _Sender(error=NotificationError("PROVIDER_UNAVAILABLE")))

    _, attempts, _, next_at = queue.failed[0]
    assert attempts == MAX_ATTEMPTS
    assert next_at is None


def test_stored_error_is_a_code_and_never_the_provider_message() -> None:
    queue = _Queue()
    error = RuntimeError(f"provider echoed api-key={SECRET} and the mail body")

    _run(queue, _Sender(error=error))

    _, _, code, _ = queue.failed[0]
    assert code == "RuntimeError"
    assert SECRET not in repr(queue.failed)
    assert "mail body" not in repr(queue.failed)


def test_secrets_in_the_provider_error_never_reach_the_logs(caplog) -> None:
    caplog.set_level("DEBUG")

    _run(_Queue(), _Sender(error=RuntimeError(f"api-key={SECRET}")))

    assert SECRET not in caplog.text


def test_failed_attempt_is_counted_in_the_report() -> None:
    report = _run(_Queue(), _Sender(error=NotificationError("PROVIDER_UNAVAILABLE")))

    assert (report.attempted, report.sent, report.failed) == (1, 0, 1)


_CLINICAL_TERMS = ("hemoglobina", "ferritina", "glucosa", "anemia", "diabetes", "diagn", "g/dl")


@pytest.mark.parametrize("tipo", sorted(TEMPLATES))
def test_every_template_is_neutral_and_carries_no_clinical_values(tipo: str) -> None:
    body = (TEMPLATES[tipo].subject + " " + TEMPLATES[tipo].text).lower()

    assert re.search(r"\d", body) is None
    assert not any(term in body for term in _CLINICAL_TERMS)

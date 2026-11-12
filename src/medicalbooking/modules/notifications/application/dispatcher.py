"""Send due notifications through the sender port and record each outcome.

Each claimed row gets one attempt per run. A failure stores only an error code (the
NotificationError code, or the exception type name for anything unexpected), never the message,
the body or any credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    EmailMessage,
    NotificationError,
    NotificationQueue,
    NotificationSender,
)
from medicalbooking.modules.notifications.domain.delivery import next_attempt_at
from medicalbooking.modules.notifications.domain.templates import TEMPLATES

# A claim holds its rows this long; a crashed worker's rows become due again after it.
LEASE = timedelta(minutes=5)
_MAX_ERROR_CODE_LENGTH = 100


@dataclass(frozen=True)
class SendReport:
    attempted: int
    sent: int
    failed: int


class NotificationDispatcher:
    def __init__(
        self,
        queue: NotificationQueue,
        sender: NotificationSender,
        clock: Callable[[], datetime],
    ) -> None:
        self._queue = queue
        self._sender = sender
        self._clock = clock

    def send_due(self, limit: int) -> SendReport:
        now = self._clock()
        claimed = self._queue.claim_due(now, now + LEASE, limit)
        sent = 0
        for notification in claimed:
            if self._attempt(notification, now):
                sent += 1
        return SendReport(attempted=len(claimed), sent=sent, failed=len(claimed) - sent)

    def _attempt(self, notification: DueNotification, now: datetime) -> bool:
        attempts = notification.numero_intentos + 1
        try:
            template = TEMPLATES[notification.tipo]
            message_id = self._sender.send(
                EmailMessage(
                    to=notification.destinatario,
                    subject=template.subject,
                    text=template.text,
                )
            )
        except Exception as error:
            code = error.code if isinstance(error, NotificationError) else type(error).__name__
            self._queue.mark_attempt_failed(
                notification.id,
                attempts,
                code[:_MAX_ERROR_CODE_LENGTH],
                next_attempt_at(attempts, now),
                now,
            )
            return False
        self._queue.mark_sent(notification.id, message_id, now)
        return True

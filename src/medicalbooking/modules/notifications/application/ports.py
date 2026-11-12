"""Ports between notification use cases and their adapters (SQL Server, Brevo).

Notification rows carry only what the sender needs: the template code and the recipient
address. Message bodies are rendered at send time from the template code, so no clinical
content is ever stored. Errors cross the boundary as codes, never as messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class PatientContact:
    user_id: str
    email: str


@dataclass(frozen=True)
class NewNotification:
    key: str
    event_id: UUID
    usuario_id: str
    tipo: str
    destinatario: str


@dataclass(frozen=True)
class DueNotification:
    id: str
    tipo: str
    destinatario: str
    numero_intentos: int


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class NotificationError(Exception):
    """A send failed. ``code`` is a short identifier, never a provider message or body."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class NotificationRepository(Protocol):
    def find_patient_contact(
        self, aggregate_type: str, aggregate_id: UUID
    ) -> PatientContact | None: ...

    def enqueue(self, notification: NewNotification, now: datetime) -> bool:
        """Insert a PENDIENTE row. Returns False when the idempotency key already exists."""
        ...


class NotificationQueue(Protocol):
    def claim_due(self, now: datetime, lease_until: datetime, limit: int) -> list[DueNotification]:
        """Lease due rows so no other process claims them until ``lease_until``."""
        ...

    def mark_sent(
        self, notification_id: str, provider_message_id: str | None, now: datetime
    ) -> None: ...

    def mark_attempt_failed(
        self,
        notification_id: str,
        attempts: int,
        error_code: str,
        next_attempt_at: datetime | None,
        now: datetime,
    ) -> None:
        """Record a failed attempt. ``next_attempt_at`` None means FALLIDA (no more attempts)."""
        ...


class NotificationSender(Protocol):
    def send(self, message: EmailMessage) -> str | None:
        """Send one message. Returns the provider message id when available."""
        ...

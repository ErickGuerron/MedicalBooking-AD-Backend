"""Ports between the outbox dispatcher and the destinations it feeds.

A handler is one destination (for example NOTIFICATIONS, later SUPABASE or MONGODB). It must
be idempotent: the dispatcher may call it again after a crash. It raises
TransientDeliveryError for a retryable failure and PermanentDeliveryError for one that will
never succeed; any other exception is treated as transient.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from medicalbooking.modules.synchronization.domain.events import OutboxEvent


class TransientDeliveryError(Exception):
    """A delivery failed for a reason that may clear up (timeout, lock, provider outage)."""


class PermanentDeliveryError(Exception):
    """A delivery failed for a reason that retries cannot fix (invalid event for this handler)."""


class OutboxHandler(Protocol):
    destination_code: str

    def handle(self, event: OutboxEvent) -> None: ...


@dataclass(frozen=True)
class DeliveryClaim:
    """One delivery leased by a dispatcher. ``lease_until`` is the fencing token for completion."""

    delivery_id: str
    destination_code: str
    attempts: int
    lease_until: datetime
    event: OutboxEvent


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str  # ENTREGADO, ERROR_TEMPORAL or ERROR_PERMANENTE
    attempts: int
    next_attempt_at: datetime | None = None
    error_code: str | None = None


class OutboxStore(Protocol):
    """Persistence port for delivery rows (one row per event and active destination)."""

    def materialize(self) -> int: ...

    def claim_due(
        self,
        now: datetime,
        lease_until: datetime,
        destination_codes: tuple[str, ...],
        limit: int,
    ) -> list[DeliveryClaim]: ...

    def complete(self, claim: DeliveryClaim, outcome: DeliveryOutcome, now: datetime) -> bool: ...

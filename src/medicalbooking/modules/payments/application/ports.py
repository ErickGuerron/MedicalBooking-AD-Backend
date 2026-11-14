"""Ports the payments use cases depend on. Adapters live in infrastructure/."""

from __future__ import annotations

from typing import Protocol

from medicalbooking.modules.payments.domain.webhook import PaymentChange


class WebhookStore(Protocol):
    def process(self, event_id: str, event_type: str, change: PaymentChange) -> str:
        """Record the event once and apply its change. Returns the outcome for the response."""
        ...

"""Stripe webhook use case (E4): verify, parse, interpret and hand the event to the store."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

from medicalbooking.modules.payments.application.ports import WebhookStore
from medicalbooking.modules.payments.domain.webhook import (
    RefundChange,
    interpret_event,
    interpret_refund_event,
    verify_signature,
)
from medicalbooking.shared.domain.errors import AppError, ServiceUnavailable


class PaymentsEventStore(WebhookStore, Protocol):
    def process_refund(self, event_id: str, event_type: str, change: RefundChange) -> str:
        """Finalize the reembolso of a refund.* event. Returns the outcome for the response."""
        ...


class InvalidPayload(AppError):
    """The signed body is not a Stripe event object."""

    status = 400
    title = "Bad Request"


class WebhookNotConfigured(ServiceUnavailable):
    """No signing secret is configured, so no event can be trusted."""


class StripeWebhookService:
    def __init__(
        self,
        store: PaymentsEventStore,
        secret: str | None,
        clock: Callable[[], int],
    ) -> None:
        self._store = store
        self._secret = secret
        self._clock = clock

    def handle(self, payload: bytes, header: str | None) -> str:
        if self._secret is None:
            raise WebhookNotConfigured("Stripe webhook secret is not configured")
        verify_signature(payload, header, self._secret, now=self._clock())
        event = _parse_event(payload)
        if event["type"].startswith("refund."):
            return self._store.process_refund(
                event["id"], event["type"], interpret_refund_event(event)
            )
        return self._store.process(event["id"], event["type"], interpret_event(event))


def _parse_event(payload: bytes) -> dict:
    try:
        event = json.loads(payload)
    except ValueError as error:
        raise InvalidPayload("body is not JSON") from error
    if not isinstance(event, dict):
        raise InvalidPayload("body is not an event object")
    if not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
        raise InvalidPayload("event lacks id or type")
    return event

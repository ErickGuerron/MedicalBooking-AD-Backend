"""Outbox destination KAFKA: publishes each outbox event to the replication topic.

The message key is the aggregate id, so Kafka keeps the order of one aggregate inside its
partition. The handler waits for the broker's answer before returning: an event counts as
delivered only when the broker acknowledged it (the producer uses acks=all and idempotence).
A rejected or unconfirmed delivery raises TransientDeliveryError, so the dispatcher retries.
The envelope identifies the event with its id: consumers deduplicate on event_id.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from medicalbooking.modules.synchronization.application.ports import TransientDeliveryError
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

FLUSH_TIMEOUT_SECONDS = 10.0


class _Producer(Protocol):
    def produce(self, topic: str, key: str, value: str, on_delivery: Any) -> None: ...

    def flush(self, timeout: float) -> int: ...


class KafkaOutboxHandler:
    destination_code = "KAFKA"

    def __init__(self, producer: _Producer, topic: str) -> None:
        self._producer = producer
        self._topic = topic

    def handle(self, event: OutboxEvent) -> None:
        outcome: dict[str, Any] = {"error": None, "delivered": False}

        def on_delivery(error: Any, _message: Any) -> None:
            outcome["error"] = error
            outcome["delivered"] = error is None

        self._producer.produce(
            topic=self._topic,
            key=str(event.aggregate_id),
            value=_envelope(event),
            on_delivery=on_delivery,
        )
        pending = self._producer.flush(FLUSH_TIMEOUT_SECONDS)
        if pending or not outcome["delivered"]:
            # Never log the broker message: it may carry data the replica must not expose.
            raise TransientDeliveryError("kafka did not confirm the event")


def _envelope(event: OutboxEvent) -> str:
    return json.dumps(
        {
            "event_id": str(event.id),
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id),
            "event_type": event.event_type,
            "aggregate_version": event.aggregate_version,
            "created_at": event.created_at.isoformat(),
            "payload": event.payload,
        },
        default=str,
    )

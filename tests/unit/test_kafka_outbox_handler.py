import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.synchronization.application.ports import TransientDeliveryError
from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler

EVENT = OutboxEvent(
    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    aggregate_type="orden_laboratorio",
    aggregate_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    event_type="orden_laboratorio.creada",
    aggregate_version=1,
    created_at=datetime(2026, 11, 12, 18, 0, tzinfo=UTC),
    payload={"estado": "SOLICITADA", "orden": {"id": "x"}},
)


class FakeProducer:
    def __init__(self, delivery_error: str | None = None, pending: int = 0) -> None:
        self.messages: list[dict] = []
        self._delivery_error = delivery_error
        self._pending = pending

    def produce(self, topic, key, value, on_delivery):
        self.messages.append({"topic": topic, "key": key, "value": value})
        on_delivery(self._delivery_error, object())

    def flush(self, timeout):
        return self._pending


def test_the_event_is_published_to_the_topic_keyed_by_its_aggregate():
    producer = FakeProducer()

    KafkaOutboxHandler(producer, topic="medicalbooking.events").handle(EVENT)

    message = producer.messages[0]
    assert message["topic"] == "medicalbooking.events"
    assert message["key"] == str(EVENT.aggregate_id)


def test_the_envelope_carries_identity_order_and_payload():
    producer = FakeProducer()

    KafkaOutboxHandler(producer, topic="medicalbooking.events").handle(EVENT)

    body = json.loads(producer.messages[0]["value"])
    assert body["event_id"] == str(EVENT.id)
    assert body["aggregate_type"] == "orden_laboratorio"
    assert body["aggregate_id"] == str(EVENT.aggregate_id)
    assert body["event_type"] == "orden_laboratorio.creada"
    assert body["aggregate_version"] == 1
    assert body["payload"] == EVENT.payload


def test_a_rejected_delivery_is_retryable():
    producer = FakeProducer(delivery_error="broker down")

    with pytest.raises(TransientDeliveryError):
        KafkaOutboxHandler(producer, topic="medicalbooking.events").handle(EVENT)


def test_messages_still_queued_after_flush_are_retryable():
    producer = FakeProducer(pending=1)

    with pytest.raises(TransientDeliveryError):
        KafkaOutboxHandler(producer, topic="medicalbooking.events").handle(EVENT)


def test_the_handler_is_wired_as_the_kafka_destination():
    handler = KafkaOutboxHandler(FakeProducer(), topic="medicalbooking.events")

    assert handler.destination_code == "KAFKA"


def test_the_same_event_always_carries_the_same_id_so_consumers_can_deduplicate():
    first, second = FakeProducer(), FakeProducer()

    KafkaOutboxHandler(first, topic="t").handle(EVENT)
    KafkaOutboxHandler(second, topic="t").handle(EVENT)

    assert json.loads(first.messages[0]["value"])["event_id"] == (
        json.loads(second.messages[0]["value"])["event_id"]
    )

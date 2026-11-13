"""An outbox event published through the production producer is read back from the topic.

Runs against the local broker when KAFKA_BOOTSTRAP_SERVERS is set; otherwise it is skipped.
Uses a fresh consumer group so it never disturbs a real replica consumer.
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime

import pytest
from confluent_kafka import Consumer

from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_producer import (
    build_kafka_producer,
)

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
TOPIC = "medicalbooking.events"
requires_broker = pytest.mark.skipif(
    not BOOTSTRAP, reason="KAFKA_BOOTSTRAP_SERVERS is not set"
)


@requires_broker
def test_an_event_round_trips_through_the_topic_with_its_key_and_envelope():
    aggregate = uuid.uuid4()
    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="orden_laboratorio",
        aggregate_id=aggregate,
        event_type="orden_laboratorio.creada",
        aggregate_version=1,
        created_at=datetime.now(UTC),
        payload={"estado": "SOLICITADA"},
    )
    producer = build_kafka_producer(BOOTSTRAP)
    KafkaOutboxHandler(producer, topic=TOPIC).handle(event)

    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"test-{uuid.uuid4()}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([TOPIC])
    found = None
    deadline = time.monotonic() + 60
    try:
        while found is None and time.monotonic() < deadline:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            if message.key() == str(aggregate).encode():
                found = message
    finally:
        consumer.close()

    assert found is not None, "the event never reached the topic"
    body = json.loads(found.value())
    assert body["event_id"] == str(event.id)
    assert body["event_type"] == "orden_laboratorio.creada"
    assert body["payload"] == {"estado": "SOLICITADA"}

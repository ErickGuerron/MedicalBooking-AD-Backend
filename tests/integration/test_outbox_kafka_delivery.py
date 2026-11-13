"""The outbox dispatcher delivers an event to the Kafka destination (kafka-replication T1).

Runs against the local broker when KAFKA_BOOTSTRAP_SERVERS is set. Only the KAFKA handler
is registered here, so the delivery row of that destination is the one under test.
"""

import json
import os
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from confluent_kafka import Consumer
from sqlalchemy import Engine, text

from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_producer import (
    build_kafka_producer,
)
from medicalbooking.modules.synchronization.infrastructure.repositories import SqlOutboxStore

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
TOPIC = "medicalbooking.events"
requires_broker = pytest.mark.skipif(not BOOTSTRAP, reason="KAFKA_BOOTSTRAP_SERVERS is not set")


@pytest.fixture
def clean_outbox(migrated_engine: Engine) -> Iterator[None]:
    with migrated_engine.begin() as conn:
        conn.execute(text("DELETE FROM notificaciones WHERE evento_origen_id IS NOT NULL"))
        conn.execute(text("DELETE FROM entregas_eventos"))
        conn.execute(text("DELETE FROM eventos_outbox"))
    yield


@requires_broker
def test_the_dispatcher_delivers_the_event_to_the_kafka_destination(
    migrated_engine: Engine, clean_outbox
) -> None:
    aggregate = uuid.uuid4()
    event_id = uuid.uuid4()
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, "
                "payload, version_agregado, creado_en) "
                "VALUES (:id, 'orden_laboratorio', :a, 'orden_laboratorio.creada', "
                "'{\"estado\": \"SOLICITADA\"}', 1, :ahora)"
            ),
            {"id": event_id, "a": aggregate, "ahora": datetime.now(UTC).replace(tzinfo=None)},
        )

    service = OutboxDispatchService(
        store=SqlOutboxStore(migrated_engine),
        handlers=[KafkaOutboxHandler(build_kafka_producer(BOOTSTRAP), topic=TOPIC)],
        clock=lambda: datetime.now(UTC),
    )
    service.run_cycle()

    with migrated_engine.connect() as conn:
        estado = conn.execute(
            text(
                "SELECT en.estado FROM entregas_eventos en "
                "JOIN destinos_sincronizacion d ON d.id = en.destino_id "
                "WHERE d.codigo = 'KAFKA' AND en.evento_id = :e"
            ),
            {"e": event_id},
        ).scalar_one()
    assert estado == "ENTREGADO"

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
            if json.loads(message.value())["event_id"] == str(event_id):
                found = message
    finally:
        consumer.close()
    assert found is not None

"""Outbox -> Kafka -> replica consumer -> Supabase, end to end (kafka-replication T5).

Each run uses its own topic, created and deleted by the test, so no earlier event can reach
the real replica. The test writes synthetic rows and removes them in a finally block.
Runs only when KAFKA_BOOTSTRAP_SERVERS, SUPABASE_WRITER_DATABASE_URL and
SUPABASE_DATABASE_URL are all set.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from confluent_kafka.admin import AdminClient, NewTopic

from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_producer import (
    build_kafka_producer,
)
from medicalbooking.workers.replica_consumer import consume

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
WRITER = os.environ.get("SUPABASE_WRITER_DATABASE_URL")
OWNER = os.environ.get("SUPABASE_DATABASE_URL")
requires_all = pytest.mark.skipif(
    not (BOOTSTRAP and WRITER and OWNER),
    reason="Kafka, Supabase writer and owner URLs are required",
)


def _create_topic(name: str) -> None:
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result()


def _delete_topic(name: str) -> None:
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.delete_topics([name]).values():
        future.result()


def _owner(sql: str, params: tuple):
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


@requires_all
def test_an_event_reaches_the_replica_once_even_when_sent_twice():
    topic = f"medicalbooking.events.test-{uuid.uuid4().hex[:12]}"
    consumer_name = f"test-{uuid.uuid4().hex[:16]}"
    user = uuid.uuid4()
    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="usuario",
        aggregate_id=user,
        event_type="usuario.activado",
        aggregate_version=2,
        created_at=datetime.now(UTC),
        payload={"usuario": {"id": str(user), "estado": "ACTIVO"}},
    )
    _create_topic(topic)
    try:
        handler = KafkaOutboxHandler(build_kafka_producer(BOOTSTRAP), topic=topic)
        handler.handle(event)
        handler.handle(event)  # the same event delivered twice (a retry after a lost ack)

        sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
        applied = consume(
            BOOTSTRAP,
            topic,
            sink,
            group_id=f"test-{uuid.uuid4().hex}",
            consumer_name=consumer_name,
            max_idle_polls=5,
        )

        assert applied == 1
        estado = _owner("SELECT estado FROM replica.usuarios WHERE id = %s", (str(user),))
        assert estado == "ACTIVO"
        processed = _owner(
            "SELECT COUNT(*) FROM replica.eventos_procesados "
            "WHERE evento_id = %s AND consumidor = %s",
            (str(event.id), consumer_name),
        )
        assert processed == 1
    finally:
        _delete_topic(topic)
        with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (str(user),))
            cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (consumer_name,))
            cur.execute(
                "DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (consumer_name,)
            )

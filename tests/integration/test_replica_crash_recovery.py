"""The replica consumer recovers from a stop and from a crash before the offset commit.

Scenarios, each on its own topic and consumer group, against the local broker and Supabase:
1. A clean stop: the consumer stops after one event; events produced while it is down are
   applied once when it restarts, and the first event is not applied again.
2. A crash after the write but before the offset commit: the restart replays that event,
   and the processed mark makes the replay a no-op.
Runs only when KAFKA_BOOTSTRAP_SERVERS, SUPABASE_WRITER_DATABASE_URL and SUPABASE_DATABASE_URL
are all set. Every row is removed in a finally block.
"""

import json
import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from confluent_kafka.admin import AdminClient, NewTopic

from medicalbooking.modules.replica.application.projection_consumer import apply_event
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


def _topic(admin: AdminClient, name: str) -> None:
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result()


def _activation(user: uuid.UUID) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="usuario",
        aggregate_id=user,
        event_type="usuario.activado",
        aggregate_version=2,
        created_at=datetime.now(UTC),
        payload={"usuario": {"id": str(user), "estado": "ACTIVO"}},
    )


def _owner_one(sql: str, params: tuple):
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _cleanup(users: list[uuid.UUID], consumer: str) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        for user in users:
            cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (str(user),))
        cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (consumer,))
        cur.execute("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (consumer,))


@requires_all
def test_a_clean_stop_and_restart_applies_each_event_exactly_once():
    topic = f"medicalbooking.events.test-{uuid.uuid4().hex[:12]}"
    group = f"test-{uuid.uuid4().hex}"
    consumer = f"test-{uuid.uuid4().hex[:16]}"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    users = [uuid.uuid4() for _ in range(5)]
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    _topic(admin, topic)
    try:
        handler = KafkaOutboxHandler(build_kafka_producer(BOOTSTRAP), topic=topic)
        for user in users[:3]:
            handler.handle(_activation(user))

        first = consume(
            BOOTSTRAP,
            topic,
            sink,
            group_id=group,
            consumer_name=consumer,
            stop_when=lambda _envelope: True,
        )
        assert first == 1

        for user in users[3:]:
            handler.handle(_activation(user))

        rest = consume(
            BOOTSTRAP,
            topic,
            sink,
            group_id=group,
            consumer_name=consumer,
            max_idle_polls=5,
        )
        assert rest == 4

        for user in users:
            assert (
                _owner_one("SELECT estado FROM replica.usuarios WHERE id = %s", (str(user),))
                == "ACTIVO"
            )
        processed = _owner_one(
            "SELECT COUNT(*) FROM replica.eventos_procesados WHERE consumidor = %s", (consumer,)
        )
        assert processed == 5
    finally:
        for future in admin.delete_topics([topic]).values():
            future.result()
        _cleanup(users, consumer)


@requires_all
def test_a_crash_before_the_offset_commit_replays_without_applying_twice():
    topic = f"medicalbooking.events.test-{uuid.uuid4().hex[:12]}"
    group = f"test-{uuid.uuid4().hex}"
    consumer = f"test-{uuid.uuid4().hex[:16]}"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    user = uuid.uuid4()
    event = _activation(user)
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    _topic(admin, topic)
    try:
        KafkaOutboxHandler(build_kafka_producer(BOOTSTRAP), topic=topic).handle(event)
        # The write commits, then the process dies before Kafka records the offset.
        envelope = {
            "event_id": str(event.id),
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id),
            "event_type": event.event_type,
            "aggregate_version": event.aggregate_version,
            "created_at": event.created_at.isoformat(),
            "payload": json.loads(json.dumps(event.payload)),
        }
        assert apply_event(envelope, sink, consumer) is True

        replayed = consume(
            BOOTSTRAP,
            topic,
            sink,
            group_id=group,
            consumer_name=consumer,
            max_idle_polls=5,
        )

        assert replayed == 0
        processed = _owner_one(
            "SELECT COUNT(*) FROM replica.eventos_procesados "
            "WHERE evento_id = %s AND consumidor = %s",
            (str(event.id), consumer),
        )
        assert processed == 1
    finally:
        for future in admin.delete_topics([topic]).values():
            future.result()
        _cleanup([user], consumer)

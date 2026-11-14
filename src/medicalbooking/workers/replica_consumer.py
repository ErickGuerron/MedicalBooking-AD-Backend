"""Replica consumer: reads the Kafka replication topic and projects it into Supabase.

    uv run python -m medicalbooking.workers.replica_consumer

Required settings: KAFKA_BOOTSTRAP_SERVERS and SUPABASE_WRITER_DATABASE_URL.

Delivery is at least once and the projection is idempotent. An offset is committed only
after its event is applied and its transaction committed. On a failed write the process
stops without committing, so a restart replays that message. A replay of an applied event
changes nothing, because the processed mark rejects it.

Only counts and error types are printed: connection strings and payloads never are.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

import psycopg
from confluent_kafka import Consumer, KafkaError

from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.projection_consumer import apply_event
from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)

logger = logging.getLogger(__name__)

CONSUMER_NAME = "replica-supabase"
GROUP_ID = "replica-supabase"
POLL_SECONDS = 1.0


def consume(
    bootstrap: str,
    topic: str,
    sink: SupabaseProjectionSink,
    *,
    group_id: str = GROUP_ID,
    consumer_name: str = CONSUMER_NAME,
    stop_when: Callable[[dict[str, Any]], bool] | None = None,
    max_idle_polls: int | None = None,
) -> int:
    """Apply messages in order. Return how many events were applied (not duplicates)."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([topic])
    applied = 0
    idle = 0
    try:
        while True:
            message = consumer.poll(POLL_SECONDS)
            if message is None:
                idle += 1
                if max_idle_polls is not None and idle >= max_idle_polls:
                    return applied
                continue
            idle = 0
            if message.error():
                if message.error().code() != KafkaError._PARTITION_EOF:
                    raise RuntimeError(f"kafka error: {message.error().code()}")
                continue
            envelope = json.loads(message.value())
            if apply_event(envelope, sink, consumer_name):
                applied += 1
            consumer.commit(message=message, asynchronous=False)
            if stop_when is not None and stop_when(envelope):
                return applied
    finally:
        consumer.close()


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
    except Exception as error:
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    if not settings.kafka_bootstrap_servers or settings.supabase_writer_database_url is None:
        logger.error(
            "worker configuration incomplete: need KAFKA_BOOTSTRAP_SERVERS and "
            "SUPABASE_WRITER_DATABASE_URL"
        )
        return 1

    writer_url = settings.supabase_writer_database_url.get_secret_value()
    sink = SupabaseProjectionSink(lambda: psycopg.connect(writer_url, connect_timeout=15))
    try:
        applied = consume(settings.kafka_bootstrap_servers, settings.kafka_topic, sink)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # Error type only: driver and payload messages may carry connection or clinical data.
        logger.error("replica consumer stopped: %s", type(error).__name__)
        return 1
    print(f"replica consumer applied={applied}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

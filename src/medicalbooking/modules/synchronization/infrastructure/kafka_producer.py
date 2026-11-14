"""Production Kafka producer for the replication topic.

acks=all and enable.idempotence=true: the broker confirms a message only once it is
stored, and a retried send never creates a duplicate in the log. Consumers still
deduplicate by event_id, because a crash between the send and the outbox update can
replay an event.
"""

from __future__ import annotations

from confluent_kafka import Producer


def build_kafka_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 5,
        }
    )

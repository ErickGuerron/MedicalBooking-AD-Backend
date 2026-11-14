"""Materialize outbox deliveries and dispatch them to their destinations.

Run once per schedule, for example every minute:

    uv run python -m medicalbooking.workers.outbox_dispatcher

Exit code 0 means the cycle finished; 1 means it failed. Only counts are printed: database
and handler errors may carry connection data, so only the error type is logged. Safe to run
from several processes at once: deliveries are leased in SQL Server, never locked in memory.

Only NOTIFICACIONES has a handler. SUPABASE and MONGODB deliveries stay PENDIENTE until their
handlers exist (E8).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

from pymongo import MongoClient
from pymongo.database import Database
from sqlalchemy import Engine

from medicalbooking.config import Settings
from medicalbooking.modules.notifications.application.handler import NotificationOutboxHandler
from medicalbooking.modules.notifications.infrastructure.repositories import SqlNotificationStore
from medicalbooking.modules.replica.infrastructure.mongo_projection import MongoOutboxHandler
from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.modules.synchronization.application.ports import OutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_producer import (
    build_kafka_producer,
)
from medicalbooking.modules.synchronization.infrastructure.repositories import SqlOutboxStore
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)


def build_handlers(
    engine: Engine,
    kafka_bootstrap: str | None = None,
    kafka_topic: str = "medicalbooking.events",
    mongo_database: Database | None = None,
) -> list[OutboxHandler]:
    """Destination handlers wired into the dispatcher, one per ready destination.

    The KAFKA destination is wired only when a broker is configured, and MONGODB only when a
    projection database is given. Without one, its delivery rows stay PENDIENTE.
    """
    handlers: list[OutboxHandler] = [
        NotificationOutboxHandler(
            repository=SqlNotificationStore(engine),
            clock=lambda: datetime.now(UTC),
        ),
    ]
    if kafka_bootstrap:
        handlers.append(
            KafkaOutboxHandler(build_kafka_producer(kafka_bootstrap), topic=kafka_topic)
        )
    if mongo_database is not None:
        handlers.append(MongoOutboxHandler(mongo_database))
    return handlers


def _mongo_projection_database(settings: Settings) -> Database | None:
    if settings.mongodb_projection_uri is None or not settings.mongodb_database:
        return None
    client = MongoClient(
        settings.mongodb_projection_uri.get_secret_value(), serverSelectionTimeoutMS=5000
    )
    return client[settings.mongodb_database]


def run(service: OutboxDispatchService) -> int:
    try:
        report = service.run_cycle()
    except Exception as error:
        # Log the error type only: the message may carry connection data.
        logger.error("outbox dispatch failed: %s", type(error).__name__)
        return 1
    print(
        f"materialized={report.materialized} delivered={report.delivered} "
        f"retry_scheduled={report.retry_scheduled} "
        f"permanent_failures={report.permanent_failures} lease_lost={report.lease_lost}"
    )
    return 0


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
        engine = create_database_engine(settings)
    except Exception as error:
        # Settings errors can echo the offending value; report the type only.
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    try:
        service = OutboxDispatchService(
            store=SqlOutboxStore(engine),
            handlers=build_handlers(
                engine,
                kafka_bootstrap=settings.kafka_bootstrap_servers,
                kafka_topic=settings.kafka_topic,
                mongo_database=_mongo_projection_database(settings),
            ),
            clock=lambda: datetime.now(UTC),
        )
        return run(service)
    except Exception as error:
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

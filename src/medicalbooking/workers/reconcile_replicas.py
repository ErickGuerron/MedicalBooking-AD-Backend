"""Reconcile replicas: requeue outbox deliveries whose replica does not match their latest event.

Run every 15 minutes, on ONE VM only. Two schedulers would requeue the same deliveries twice:

    uv run python -m medicalbooking.workers.reconcile_replicas
    uv run python -m medicalbooking.workers.reconcile_replicas --days 30

Phase 1 checks the usuario and cita aggregates whose latest event was created within the lookback
window (--days, default 30). Each aggregate is checked against MongoDB (MONGODB_PROJECTION_URI and
MONGODB_DATABASE) and Supabase (SUPABASE_DATABASE_URL). A check whose settings are unset
is skipped, never failed, and the output says so. A mismatch requeues that event's delivery; the
outbox dispatcher then applies it.

Prints: aggregates=<n> repairs_mongodb=<n> repairs_supabase=<n> skipped_supabase=<bool>
The repair counts are requeues that changed a delivery; a repeat run counts only new changes.
Exit code 0 means the cycle finished; 1 means it failed or could not start. Only counts are
printed: connection strings, payloads and event ids never are.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

import psycopg
from pymongo import MongoClient
from pymongo.database import Database
from sqlalchemy import Engine

from medicalbooking.config import Settings
from medicalbooking.modules.replica.infrastructure.supabase_presence import (
    DESTINATION_CODE as SUPABASE_DESTINATION,
)
from medicalbooking.modules.replica.infrastructure.supabase_presence import (
    SupabasePresenceCheck,
)
from medicalbooking.modules.synchronization.application.reconciliation import (
    ReplicaReconciler,
)
from medicalbooking.modules.synchronization.infrastructure.mongo_presence import (
    DESTINATION_CODE as MONGO_DESTINATION,
)
from medicalbooking.modules.synchronization.infrastructure.mongo_presence import (
    MongoPresenceCheck,
)
from medicalbooking.modules.synchronization.infrastructure.reconciliation_repositories import (
    SqlReconciliationStore,
)
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MONGO_TIMEOUT_MS = 5000
SUPABASE_CONNECT_TIMEOUT_S = 15


def _positive_days(value: str) -> int:
    days = int(value)
    if days < 1:
        raise argparse.ArgumentTypeError("--days must be at least 1")
    return days


def _parse_days(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reconcile_replicas", add_help=False)
    parser.add_argument("--days", type=_positive_days, default=DEFAULT_DAYS)
    return parser.parse_args(argv).days


def _mongo_connection(settings: Settings) -> tuple[MongoClient, Database] | None:
    if settings.mongodb_projection_uri is None or not settings.mongodb_database:
        return None
    client: MongoClient = MongoClient(
        settings.mongodb_projection_uri.get_secret_value(),
        serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
    )
    return client, client[settings.mongodb_database]


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        days = _parse_days(sys.argv[1:])
    except SystemExit:
        # argparse has already printed the usage error to stderr.
        return 1
    try:
        settings = Settings()
    except Exception as error:
        # Settings errors can echo the offending value; report the type only.
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1

    engine: Engine | None = None
    mongo: tuple[MongoClient, Database] | None = None
    try:
        engine = create_database_engine(settings)
        store = SqlReconciliationStore(engine)
        targets = []

        mongo = _mongo_connection(settings)
        if mongo is None:
            logger.warning(
                "mongodb check skipped: MONGODB_PROJECTION_URI or MONGODB_DATABASE unset"
            )
        else:
            targets.append(MongoPresenceCheck(mongo[1]))

        skipped_supabase = settings.supabase_database_url is None
        if skipped_supabase:
            logger.warning("supabase check skipped: SUPABASE_DATABASE_URL unset")
        else:
            reconciler_url = settings.supabase_database_url.get_secret_value()
            targets.append(
                SupabasePresenceCheck(
                    lambda: psycopg.connect(
                        reconciler_url, connect_timeout=SUPABASE_CONNECT_TIMEOUT_S
                    )
                )
            )

        report = ReplicaReconciler(source=store, targets=targets, repairs=store).run(
            since=datetime.now(UTC) - timedelta(days=days)
        )
    except Exception as error:
        # Database and driver errors may carry connection data: report the type only.
        logger.error("replica reconciliation failed: %s", type(error).__name__)
        return 1
    finally:
        if mongo is not None:
            mongo[0].close()
        if engine is not None:
            engine.dispose()

    print(
        f"aggregates={report.aggregates} "
        f"repairs_mongodb={report.repairs.get(MONGO_DESTINATION, 0)} "
        f"repairs_supabase={report.repairs.get(SUPABASE_DESTINATION, 0)} "
        f"skipped_supabase={skipped_supabase}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

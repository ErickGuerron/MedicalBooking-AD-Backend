"""Sync external writes: queue the appointments written outside the API for replica delivery.

Run every 15 minutes, on ONE VM only:

    uv run python -m medicalbooking.workers.sync_external_writes

It reads SQL Server only. Rows whose rowversion is above the stored watermark become
``cita.actualizada`` outbox events, and the watermark advances in the same transaction. The outbox
dispatcher delivers them to Supabase and MongoDB, retrying while a replica is down, so this worker
runs during an outage too.

Hard deletes are not detected (phase 2). Users (usuarios) are not synced yet.

Prints: queued=<n>. Exit code 0 means the cycle finished; 1 means it failed or could not start.
Only counts are printed: connection strings, payloads and row contents never are.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import Engine

from medicalbooking.config import Settings
from medicalbooking.modules.synchronization.application.external_writes import run_cycle
from medicalbooking.modules.synchronization.infrastructure.changed_citas import (
    SqlChangedCitasReader,
)
from medicalbooking.modules.synchronization.infrastructure.external_write_publisher import (
    SqlOutboxPublisher,
)
from medicalbooking.modules.synchronization.infrastructure.watermark_store import (
    SqlWatermarkStore,
)
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)

TABLE_CITAS = "citas"
BATCH_SIZE = 500


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
    except Exception as error:
        # Settings errors can echo the offending value; report the type only.
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1

    engine: Engine | None = None
    try:
        engine = create_database_engine(settings)
        queued = run_cycle(
            reader=SqlChangedCitasReader(engine),
            watermarks=SqlWatermarkStore(engine),
            publisher=SqlOutboxPublisher(engine),
            table=TABLE_CITAS,
            batch_size=BATCH_SIZE,
        )
    except Exception as error:
        # Database and driver errors may carry connection data: report the type only.
        logger.error("external write sync failed: %s", type(error).__name__)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(f"queued={queued}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

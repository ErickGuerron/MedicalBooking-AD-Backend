"""Retry checkout sessions whose provider creation failed and whose retry is due.

Run once per schedule, for example every minute:

    uv run python -m medicalbooking.workers.retry_failed_checkouts

Exit code 0 means the run finished; 1 means it failed. Details are never printed: the provider
or database error may carry connection data. Safe to run from several processes at once,
because each retry uses a key derived from its attempt and number.
"""

from __future__ import annotations

import logging
import sys

from medicalbooking.app import build_billing
from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)

RETRY_BATCH_SIZE = 50


def run(service) -> int:
    try:
        result = service.retry_due_checkouts(RETRY_BATCH_SIZE)
    except Exception as error:
        # Log the error type only: the message may carry connection data.
        logger.error("checkout retry run failed: %s", type(error).__name__)
        return 1
    print(
        f"checkout_retries attempted={result.attempted} "
        f"succeeded={result.succeeded} failed={result.failed}"
    )
    return 0


def main() -> int:
    try:
        settings = Settings()
        engine = create_database_engine(settings)
    except Exception as error:
        # Settings errors can echo the offending value; report the type only.
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    try:
        service = build_billing(settings, engine)
        return run(service)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

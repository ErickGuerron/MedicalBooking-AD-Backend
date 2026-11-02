"""Release pending appointment holds whose payment window has ended.

Run once per schedule, for example every few minutes:

    uv run python -m medicalbooking.workers.expire_holds

Exit code 0 means the sweep finished; 1 means it failed. Details are never printed: the
database error may contain connection data. Safe to run from several processes at once.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

from medicalbooking.config import Settings
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.infrastructure.repositories import SqlSchedulingStore
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)


def run(service: SchedulingService) -> int:
    try:
        expired = service.expire_overdue_holds()
    except Exception as error:
        # Log the error type only: the message may carry connection data.
        logger.error("hold expiration failed: %s", type(error).__name__)
        return 1
    print(f"expired_holds={expired}")
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
        service = SchedulingService(
            store=SqlSchedulingStore(engine),
            clock=lambda: datetime.now(UTC),
            hold_minutes=settings.appointment_hold_minutes,
        )
        return run(service)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

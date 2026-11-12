"""Send due patient notifications through Brevo, with backoff and a bounded attempt count.

Run once per schedule, for example every minute:

    uv run python -m medicalbooking.workers.send_notifications

Required settings: BREVO_API_KEY and MAIL_FROM (MAIL_FROM_NAME is optional). Each run claims a
batch of due rows under a lease, so several processes can run at once without double sends.

Exit code 0 means the run finished (individual failed sends are retried or marked FALLIDA and
counted); 1 means the run failed or the configuration is incomplete. Details are never printed:
the provider or database error may carry the api key or connection data. Only counts are printed.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

from medicalbooking.config import Settings
from medicalbooking.modules.notifications.application.dispatcher import NotificationDispatcher
from medicalbooking.modules.notifications.infrastructure.brevo import BrevoNotificationSender
from medicalbooking.modules.notifications.infrastructure.repositories import SqlNotificationStore
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def missing_configuration(settings: Settings) -> list[str]:
    """Names of the required settings that are not set. Values are never returned."""
    missing = []
    if settings.brevo_api_key is None:
        missing.append("BREVO_API_KEY")
    if not settings.mail_from:
        missing.append("MAIL_FROM")
    return missing


def run(dispatcher) -> int:
    try:
        report = dispatcher.send_due(BATCH_SIZE)
    except Exception as error:
        # Log the error type only: the message may carry the api key or connection data.
        logger.error("notification run failed: %s", type(error).__name__)
        return 1
    print(f"notifications attempted={report.attempted} sent={report.sent} failed={report.failed}")
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
        missing = missing_configuration(settings)
        if missing:
            logger.error("worker configuration incomplete: missing %s", ", ".join(missing))
            return 1
        dispatcher = NotificationDispatcher(
            queue=SqlNotificationStore(engine),
            sender=BrevoNotificationSender(
                api_key=settings.brevo_api_key.get_secret_value(),
                sender_email=settings.mail_from,
                sender_name=settings.mail_from_name,
            ),
            clock=lambda: datetime.now(UTC),
        )
        return run(dispatcher)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

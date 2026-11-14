"""Reconcile refunds left PENDIENTE by an unknown provider outcome.

Run periodically, for example every 15 minutes:

    uv run python -m medicalbooking.workers.reconcile_refunds

A refund pending for more than 15 minutes is checked at Stripe. A final answer is applied through
the same path as the refund webhook, and a refund that never reached Stripe is looked up by its
metadata or sent again with its original idempotency key.

Exit code 0 means the cycle finished; 1 means it could not start or failed. Needs the SQL Server
settings and STRIPE_SECRET_KEY; without the key nothing is read or changed. Only counts are
printed, never refund ids or provider text. Safe to run repeatedly: a repeat changes nothing.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine

from medicalbooking.config import Settings
from medicalbooking.modules.payments.application.refund_reconciliation import (
    ReconciliationGateway,
    RefundReconciliationService,
)
from medicalbooking.modules.payments.infrastructure.refund_reconciliation import (
    list_stale_pending_refunds,
)
from medicalbooking.modules.payments.infrastructure.refund_repositories import SqlRefundStore
from medicalbooking.modules.payments.infrastructure.repositories import SqlPaymentsStore
from medicalbooking.modules.payments.infrastructure.stripe_checkout import StripeCheckoutGateway
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)


def build_service(
    engine: Engine,
    gateway: ReconciliationGateway,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RefundReconciliationService:
    return RefundReconciliationService(
        find_stale=lambda cutoff: list_stale_pending_refunds(engine, cutoff),
        refunds=SqlRefundStore(engine),
        payments=SqlPaymentsStore(engine, clock=clock),
        gateway=gateway,
        clock=clock,
    )


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
    key = settings.stripe_secret_key
    if key is None or not key.get_secret_value():
        logger.error("STRIPE_SECRET_KEY is not configured; nothing was reconciled")
        return 1
    engine: Engine | None = None
    try:
        engine = create_database_engine(settings)
        gateway = StripeCheckoutGateway(secret_key=key.get_secret_value(), return_url="")
        report = build_service(engine, gateway).run()
    except Exception as error:
        # Database and provider errors may carry connection data: report the type only.
        logger.error("refund reconciliation failed: %s", type(error).__name__)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(
        f"examined={report.examined} finalized={report.finalized} "
        f"already_applied={report.already_applied} failed={report.failed} "
        f"kept_pending={report.kept_pending}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

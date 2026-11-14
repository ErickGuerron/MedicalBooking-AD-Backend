"""Outbox dispatch rules: materialize deliveries, then deliver them in per-aggregate order.

Handlers run outside any database transaction. A claim holds a lease, and the outcome is
written only if the lease is still ours, so a dispatcher that stalled past its lease cannot
overwrite the result of the dispatcher that took over.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from medicalbooking.modules.synchronization.application.ports import (
    DeliveryClaim,
    DeliveryOutcome,
    OutboxHandler,
    OutboxStore,
    PermanentDeliveryError,
)
from medicalbooking.modules.synchronization.domain.retry import MAX_ATTEMPTS, backoff_after


@dataclass
class DispatchReport:
    materialized: int = 0
    delivered: int = 0
    retry_scheduled: int = 0
    permanent_failures: int = 0
    lease_lost: int = 0


class OutboxDispatchService:
    def __init__(
        self,
        store: OutboxStore,
        handlers: Sequence[OutboxHandler],
        clock: Callable[[], datetime],
        lease: timedelta = timedelta(minutes=5),
        batch_size: int = 50,
    ) -> None:
        self._store = store
        self._handlers = {handler.destination_code: handler for handler in handlers}
        if len(self._handlers) != len(handlers):
            raise ValueError("each destination code may have only one handler")
        self._clock = clock
        self._lease = lease
        self._batch_size = batch_size

    def materialize(self) -> int:
        return self._store.materialize()

    def run_cycle(self) -> DispatchReport:
        report = DispatchReport(materialized=self.materialize())
        if not self._handlers:
            return report
        codes = tuple(sorted(self._handlers))
        while True:
            now = self._clock()
            claims = self._store.claim_due(now, now + self._lease, codes, self._batch_size)
            if not claims:
                return report
            for claim in claims:
                self._deliver(claim, report)

    def _deliver(self, claim: DeliveryClaim, report: DispatchReport) -> None:
        handler = self._handlers[claim.destination_code]
        attempts = claim.attempts + 1
        try:
            handler.handle(claim.event)
            outcome = DeliveryOutcome("ENTREGADO", attempts)
        except Exception as error:
            outcome = self._failure(error, attempts, self._clock())
        applied = self._store.complete(claim, outcome, self._clock())
        if not applied:
            report.lease_lost += 1
        elif outcome.status == "ENTREGADO":
            report.delivered += 1
        elif outcome.status == "ERROR_TEMPORAL":
            report.retry_scheduled += 1
        else:
            report.permanent_failures += 1

    @staticmethod
    def _failure(error: Exception, attempts: int, now: datetime) -> DeliveryOutcome:
        # Only the class name is stored: the message may carry connection data.
        code = type(error).__name__
        if isinstance(error, PermanentDeliveryError) or attempts >= MAX_ATTEMPTS:
            return DeliveryOutcome("ERROR_PERMANENTE", attempts, error_code=code)
        return DeliveryOutcome(
            "ERROR_TEMPORAL",
            attempts,
            next_attempt_at=now + backoff_after(attempts),
            error_code=code,
        )

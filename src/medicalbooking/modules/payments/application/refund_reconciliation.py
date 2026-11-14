"""Reconcile refunds left PENDIENTE by an unknown provider outcome (payments, refunds v1).

A refund is stale when it has stayed PENDIENTE for longer than STALE_AFTER. The provider is then
asked what happened, and any final answer is applied through SqlPaymentsStore.process_refund, the
same path the Stripe webhook uses. Its event id is derived from the Stripe refund and the target
status, so a repeated run is recorded as DUPLICADO and changes nothing.

Logs carry counts and error types only: never refund ids, payment data or provider text.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.application.refund_service import (
    REEMBOLSO_PENDIENTE,
    ProviderRejected,
)
from medicalbooking.modules.payments.domain.billing import to_cents
from medicalbooking.modules.payments.domain.webhook import (
    PROCESADO,
    RefundChange,
    refund_target_status,
)

STALE_AFTER = timedelta(minutes=15)
RECONCILE_EVENT_TYPE = "refund.reconciled"
REEMBOLSO_METADATA_KEY = "reembolso_id"
logger = logging.getLogger(__name__)

FINALIZED = "finalized"
ALREADY_APPLIED = "already_applied"
FAILED = "failed"
KEPT_PENDING = "kept_pending"


@dataclass(frozen=True)
class StaleRefund:
    id: str
    stripe_refund_id: str | None
    payment_intent_id: str | None
    monto: Decimal
    solicitado_en: datetime


@dataclass(frozen=True)
class ReconciliationReport:
    examined: int
    finalized: int
    already_applied: int
    failed: int
    kept_pending: int


class ProviderRefundView(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def metadata(self) -> Mapping[str, str]: ...


class ReconciliationGateway(Protocol):
    def get_refund(self, stripe_refund_id: str) -> ProviderRefundView: ...

    def list_refunds_for_payment_intent(
        self, payment_intent_id: str
    ) -> Sequence[ProviderRefundView]: ...

    def create_refund(
        self,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
        metadata: Mapping[str, str],
    ) -> ProviderRefundView: ...


class RefundStateStore(Protocol):
    def mark_failed(self, refund_id: str) -> None: ...

    def record_provider_answer(
        self, refund_id: str, stripe_refund_id: str, estado: str
    ) -> None: ...


class PaymentsRefundStore(Protocol):
    def process_refund(self, event_id: str, event_type: str, change: RefundChange) -> str: ...


class RefundReconciliationService:
    def __init__(
        self,
        *,
        find_stale: Callable[[datetime], Sequence[StaleRefund]],
        refunds: RefundStateStore,
        payments: PaymentsRefundStore,
        gateway: ReconciliationGateway,
        clock: Callable[[], datetime],
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self._find_stale = find_stale
        self._refunds = refunds
        self._payments = payments
        self._gateway = gateway
        self._clock = clock
        self._stale_after = stale_after

    def run(self) -> ReconciliationReport:
        cutoff = self._clock() - self._stale_after
        outcomes = [self._reconcile(refund) for refund in self._find_stale(cutoff)]
        report = ReconciliationReport(
            examined=len(outcomes),
            finalized=outcomes.count(FINALIZED),
            already_applied=outcomes.count(ALREADY_APPLIED),
            failed=outcomes.count(FAILED),
            kept_pending=outcomes.count(KEPT_PENDING),
        )
        logger.info(
            "refund reconciliation: examined=%d finalized=%d already_applied=%d failed=%d "
            "kept_pending=%d",
            report.examined,
            report.finalized,
            report.already_applied,
            report.failed,
            report.kept_pending,
        )
        return report

    def _reconcile(self, refund: StaleRefund) -> str:
        if refund.stripe_refund_id is None:
            return self._recover_missing_id(refund)
        return self._apply_provider_status(refund.id, refund.stripe_refund_id)

    def _apply_provider_status(self, reembolso_id: str, stripe_refund_id: str) -> str:
        try:
            remote = self._gateway.get_refund(stripe_refund_id)
        except ProviderUnavailable as error:
            return _kept(error)
        target = refund_target_status(remote.status)
        if target is None:
            return KEPT_PENDING
        outcome = self._payments.process_refund(
            f"reconcile-{stripe_refund_id}-{target}",
            RECONCILE_EVENT_TYPE,
            RefundChange(refund_id=stripe_refund_id, target_status=target),
        )
        return FINALIZED if outcome == PROCESADO else ALREADY_APPLIED

    def _recover_missing_id(self, refund: StaleRefund) -> str:
        """A refund that never got a Stripe id: find it at the provider, or send it again.

        The resend uses the same idempotency key as the original request, so Stripe answers with
        the first result if that request did arrive. Without a payment intent nothing could have
        been sent, so the refund is marked failed (the request path refuses it the same way)."""
        if refund.payment_intent_id is None:
            self._refunds.mark_failed(refund.id)
            return FAILED
        try:
            listed = self._gateway.list_refunds_for_payment_intent(refund.payment_intent_id)
        except ProviderUnavailable as error:
            return _kept(error)
        match = next(
            (r for r in listed if r.metadata.get(REEMBOLSO_METADATA_KEY) == refund.id), None
        )
        if match is not None:
            stripe_refund_id = match.id
        else:
            try:
                created = self._gateway.create_refund(
                    refund.payment_intent_id,
                    to_cents(refund.monto),
                    f"refund-{refund.id}",
                    {REEMBOLSO_METADATA_KEY: refund.id},
                )
            except ProviderRejected:
                self._refunds.mark_failed(refund.id)
                logger.warning("refund marked failed: provider refused the resend")
                return FAILED
            except ProviderUnavailable as error:
                return _kept(error)
            stripe_refund_id = created.id
        self._refunds.record_provider_answer(refund.id, stripe_refund_id, REEMBOLSO_PENDIENTE)
        return self._apply_provider_status(refund.id, stripe_refund_id)


def _kept(error: Exception) -> str:
    logger.warning("refund left pending: provider unavailable (%s)", type(error).__name__)
    return KEPT_PENDING

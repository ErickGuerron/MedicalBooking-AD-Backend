"""SQL Server query for the refunds the reconciler must look at (payments, refunds v1).

A refund is a candidate when it is still PENDIENTE and was requested before the cutoff. Only
reads: the state changes go through the existing refund and webhook stores. Parameterized SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.refund_reconciliation import StaleRefund

_STALE_PENDING = text(
    "SELECT r.id, r.stripe_refund_id, r.monto, r.solicitado_en, "
    "p.stripe_payment_intent_id "
    "FROM reembolsos AS r "
    "JOIN pagos AS p ON p.id = r.pago_id "
    "WHERE r.estado = 'PENDIENTE' AND r.solicitado_en < :antes "
    "ORDER BY r.solicitado_en"
)


def list_stale_pending_refunds(engine: Engine, older_than: datetime) -> list[StaleRefund]:
    """PENDIENTE refunds requested before `older_than`, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(_STALE_PENDING, {"antes": _utc_naive(older_than)}).all()
    return [
        StaleRefund(
            id=str(row.id).lower(),
            stripe_refund_id=row.stripe_refund_id,
            payment_intent_id=row.stripe_payment_intent_id,
            monto=Decimal(row.monto),
            solicitado_en=row.solicitado_en.replace(tzinfo=UTC),
        )
        for row in rows
    ]


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)

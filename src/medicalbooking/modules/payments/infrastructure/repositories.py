"""SQL Server adapter for the Stripe webhook (E4).

The whole event is one transaction: the inbox row in stripe_eventos, the payment and invoice
changes, and the final event state commit together or not at all. A failure rolls the inbox
row back, so Stripe's retry is processed as a first delivery. A duplicate is detected under
an update lock, so two concurrent deliveries of the same event cannot both apply a change.
Parameterized SQL only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.domain.webhook import (
    CONFIRMADO,
    DUPLICADO,
    IGNORADO,
    PROCESADO,
    PagoSnapshot,
    PaymentChange,
    RefundChange,
    decide,
)
from medicalbooking.modules.payments.infrastructure.billing_repositories import (
    emit_pago_snapshot,
    emit_reembolso_snapshot,
    set_payment_intent,
)
from medicalbooking.modules.payments.infrastructure.refund_finalization import (
    finalize_reembolso,
    find_reembolso_id,
    mark_invoice_refunded,
)

logger = logging.getLogger(__name__)


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


class SqlPaymentsStore:
    def __init__(self, engine: Engine, clock: Callable[[], datetime]) -> None:
        self._engine = engine
        self._clock = clock

    def process(self, event_id: str, event_type: str, change: PaymentChange) -> str:
        now = _utc_naive(self._clock())
        with self._engine.begin() as conn:
            if self._already_recorded(conn, event_id):
                return DUPLICADO
            self._record_inbox(conn, event_id, event_type, now)
            pago = self._pago_for_session(conn, change.session_id)
            if pago is not None and change.payment_intent is not None:
                # NULL-guarded: a replay or a later event never overwrites the stored intent.
                set_payment_intent(conn, pago.pago_id, change.payment_intent)
            decision = decide(change, pago)
            if decision.outcome == PROCESADO and pago is not None:
                self._apply(conn, pago, decision.target_status or "", now)
            self._close_inbox(conn, event_id, decision.outcome, decision.detail, now)
            return decision.outcome

    def process_refund(self, event_id: str, event_type: str, change: RefundChange) -> str:
        """Finalize the reembolso of a refund.* event. Unknown refunds are ignored, unwritten."""
        now = _utc_naive(self._clock())
        with self._engine.begin() as conn:
            if self._already_recorded(conn, event_id):
                outcome = DUPLICADO
            else:
                reembolso_id = (
                    find_reembolso_id(conn, change.refund_id) if change.refund_id else None
                )
                if reembolso_id is None:
                    outcome = IGNORADO
                else:
                    outcome = self._finalize_refund(
                        conn, event_id, event_type, reembolso_id, change, now
                    )
        logger.info("stripe refund event %s mapped to %s", event_type, outcome)
        return outcome

    @staticmethod
    def _finalize_refund(
        conn: Connection,
        event_id: str,
        event_type: str,
        reembolso_id: str,
        change: RefundChange,
        now: datetime,
    ) -> str:
        SqlPaymentsStore._record_inbox(conn, event_id, event_type, now)
        if change.target_status is None:
            outcome, detail = IGNORADO, "evento sin cambio de reembolso"
        elif finalize_reembolso(conn, reembolso_id, change.target_status, now):
            if change.target_status == "PROCESADO":
                mark_invoice_refunded(conn, reembolso_id)
            emit_reembolso_snapshot(conn, reembolso_id, now)
            outcome, detail = PROCESADO, None
        else:
            outcome, detail = IGNORADO, "reembolso ya no pendiente"
        SqlPaymentsStore._close_inbox(conn, event_id, outcome, detail, now)
        return outcome

    @staticmethod
    def _record_inbox(conn: Connection, event_id: str, event_type: str, now: datetime) -> None:
        conn.execute(
            text(
                "INSERT INTO stripe_eventos "
                "(id, stripe_event_id, tipo_evento, estado, intentos, recibido_en) "
                "VALUES (:id, :evento, :tipo, 'PENDIENTE', 1, :ahora)"
            ),
            {"id": str(uuid.uuid4()), "evento": event_id, "tipo": event_type, "ahora": now},
        )

    @staticmethod
    def _close_inbox(
        conn: Connection, event_id: str, outcome: str, detail: str | None, now: datetime
    ) -> None:
        conn.execute(
            text(
                "UPDATE stripe_eventos SET estado = :estado, procesado_en = :ahora, "
                "error_detalle = :detalle WHERE stripe_event_id = :evento"
            ),
            {"estado": outcome, "ahora": now, "detalle": detail, "evento": event_id},
        )

    @staticmethod
    def _already_recorded(conn: Connection, event_id: str) -> bool:
        found = conn.execute(
            text(
                "SELECT 1 FROM stripe_eventos WITH (UPDLOCK, HOLDLOCK) "
                "WHERE stripe_event_id = :evento"
            ),
            {"evento": event_id},
        ).first()
        return found is not None

    @staticmethod
    def _pago_for_session(conn: Connection, session_id: str | None) -> PagoSnapshot | None:
        if session_id is None:
            return None
        row = conn.execute(
            text(
                "SELECT p.id, p.factura_id, p.estado, p.monto, p.moneda, "
                "f.estado AS factura_estado "
                "FROM pagos AS p WITH (UPDLOCK) "
                "JOIN facturas AS f ON f.id = p.factura_id "
                "WHERE p.stripe_checkout_session_id = :sesion"
            ),
            {"sesion": session_id},
        ).first()
        if row is None:
            return None
        return PagoSnapshot(
            pago_id=str(row.id).lower(),
            factura_id=str(row.factura_id).lower(),
            estado=row.estado,
            monto=Decimal(row.monto),
            moneda=row.moneda.strip(),
            factura_estado=row.factura_estado,
        )

    @staticmethod
    def _apply(conn: Connection, pago: PagoSnapshot, target: str, now: datetime) -> None:
        conn.execute(
            text(
                "UPDATE pagos SET estado = :estado, "
                "pagado_en = CASE WHEN :estado = 'CONFIRMADO' THEN :ahora ELSE pagado_en END "
                "WHERE id = :id AND estado = 'PENDIENTE'"
            ),
            {"estado": target, "ahora": now, "id": pago.pago_id},
        )
        if target == CONFIRMADO:
            conn.execute(
                text(
                    "UPDATE facturas SET estado = 'PAGADA' "
                    "WHERE id = :factura AND estado = 'PENDIENTE'"
                ),
                {"factura": pago.factura_id},
            )
        emit_pago_snapshot(conn, pago.pago_id, now)

    
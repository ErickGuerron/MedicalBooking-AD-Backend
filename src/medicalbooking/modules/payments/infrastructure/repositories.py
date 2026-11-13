"""SQL Server adapter for the Stripe webhook (E4).

The whole event is one transaction: the inbox row in stripe_eventos, the payment and invoice
changes, and the final event state commit together or not at all. A failure rolls the inbox
row back, so Stripe's retry is processed as a first delivery. A duplicate is detected under
an update lock, so two concurrent deliveries of the same event cannot both apply a change.
Parameterized SQL only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.domain.webhook import (
    CONFIRMADO,
    DUPLICADO,
    PROCESADO,
    PagoSnapshot,
    PaymentChange,
    decide,
)
from medicalbooking.modules.payments.infrastructure.billing_repositories import emit_pago_snapshot


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
            conn.execute(
                text(
                    "INSERT INTO stripe_eventos "
                    "(id, stripe_event_id, tipo_evento, estado, intentos, recibido_en) "
                    "VALUES (:id, :evento, :tipo, 'PENDIENTE', 1, :ahora)"
                ),
                {"id": str(uuid.uuid4()), "evento": event_id, "tipo": event_type, "ahora": now},
            )
            pago = self._pago_for_session(conn, change.session_id)
            decision = decide(change, pago)
            if decision.outcome == PROCESADO and pago is not None:
                self._apply(conn, pago, decision.target_status or "", now)
            conn.execute(
                text(
                    "UPDATE stripe_eventos SET estado = :estado, procesado_en = :ahora, "
                    "error_detalle = :detalle WHERE stripe_event_id = :evento"
                ),
                {
                    "estado": decision.outcome,
                    "ahora": now,
                    "detalle": decision.detail,
                    "evento": event_id,
                },
            )
            return decision.outcome

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

    
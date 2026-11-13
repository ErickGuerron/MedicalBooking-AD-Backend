"""SQL for finalizing a reembolso from a Stripe refund.updated event (payments, refunds v1).

Runs inside the caller's transaction (SqlPaymentsStore.process_refund). The transition is
guarded by ``estado = 'PENDIENTE'``, so a late or replayed event cannot move a reembolso that
is already final. Parameterized SQL only.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, text


def find_reembolso_id(conn: Connection, stripe_refund_id: str) -> str | None:
    row = conn.execute(
        text("SELECT id FROM reembolsos WHERE stripe_refund_id = :refund"),
        {"refund": stripe_refund_id},
    ).first()
    return None if row is None else str(row.id).lower()


def finalize_reembolso(conn: Connection, reembolso_id: str, target: str, now: datetime) -> bool:
    """Move a PENDIENTE reembolso to ``target``; return False when it was not pending."""
    result = conn.execute(
        text(
            "UPDATE reembolsos SET estado = :estado, "
            "procesado_en = CASE WHEN :estado = 'PROCESADO' THEN :ahora ELSE procesado_en END "
            "WHERE id = :id AND estado = 'PENDIENTE'"
        ),
        {"estado": target, "ahora": now, "id": reembolso_id},
    )
    return result.rowcount == 1

def mark_invoice_refunded(conn: Connection, reembolso_id: str) -> bool:
    """Move the invoice of a refunded payment from PAGADA to REEMBOLSADA (guarded)."""
    result = conn.execute(
        text(
            "UPDATE f SET f.estado = 'REEMBOLSADA' "
            "FROM facturas AS f "
            "JOIN pagos AS p ON p.factura_id = f.id "
            "JOIN reembolsos AS r ON r.pago_id = p.id "
            "WHERE r.id = :id AND f.estado = 'PAGADA'"
        ),
        {"id": reembolso_id},
    )
    return result.rowcount == 1
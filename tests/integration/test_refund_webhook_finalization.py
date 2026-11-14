"""Stripe refund.updated finalization against real SQL Server (refunds, webhook)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.application.webhook_service import StripeWebhookService
from medicalbooking.modules.payments.domain.webhook import interpret_event, interpret_refund_event
from medicalbooking.modules.payments.infrastructure.repositories import SqlPaymentsStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SECRET = "whsec_test_synthetic_secret"


def _id() -> str:
    return str(uuid.uuid4())


def _seed_pago(conn: Connection, *, session_id: str, monto: str = "50.00") -> dict[str, str]:
    usuario, paciente, factura, pago = _id(), _id(), _id(), _id()
    conn.execute(
        text(
            "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
            "VALUES (:id, :email, N'Nombre', N'Apellido', SYSUTCDATETIME(), SYSUTCDATETIME())"
        ),
        {"id": usuario, "email": f"{usuario}@example.test"},
    )
    conn.execute(
        text(
            "INSERT INTO pacientes (id, usuario_id, creado_en) VALUES (:id, :u, SYSUTCDATETIME())"
        ),
        {"id": paciente, "u": usuario},
    )
    conn.execute(
        text(
            "INSERT INTO facturas (id, paciente_id, subtotal, impuestos, total, moneda, estado, "
            "creada_en) VALUES (:id, :p, :m, 0, :m, 'USD', 'PENDIENTE', SYSUTCDATETIME())"
        ),
        {"id": factura, "p": paciente, "m": monto},
    )
    conn.execute(
        text(
            "INSERT INTO pagos (id, factura_id, clave_idempotencia, monto, moneda, estado, "
            "creado_en, stripe_checkout_session_id) VALUES (:id, :f, :k, :m, 'USD', 'PENDIENTE', "
            "SYSUTCDATETIME(), :s)"
        ),
        {"id": pago, "f": factura, "k": f"key-{pago}", "m": monto, "s": session_id},
    )
    return {"pago": pago, "factura": factura}


def _checkout_event(session_id: str, amount: int = 5000) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "payment_status": "paid",
                "amount_total": amount,
                "currency": "usd",
            }
        },
    }


def _refund_event(refund_id: str, status: str) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "refund.updated",
        "data": {"object": {"id": refund_id, "status": status, "amount": 5000}},
    }


def _paid_pago_with_reembolso(engine: Engine) -> dict[str, str]:
    """A CONFIRMADO payment, its PAGADA invoice, and a PENDIENTE reembolso for it."""
    session = f"cs_test_{uuid.uuid4().hex}"
    with engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    checkout = _checkout_event(session)
    # Confirms the payment through the existing checkout path, as the other suite does.
    SqlPaymentsStore(engine, clock=lambda: NOW).process(
        checkout["id"], checkout["type"], interpret_event(checkout)
    )
    refund_id = f"re_test_{uuid.uuid4().hex}"
    reembolso = _id()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reembolsos (id, pago_id, stripe_refund_id, monto, estado, motivo, "
                "solicitado_en) VALUES (:id, :p, :r, 50.00, 'PENDIENTE', N'CANCELACION_PACIENTE', "
                "SYSUTCDATETIME())"
            ),
            {"id": reembolso, "p": ids["pago"], "r": refund_id},
        )
    return {**ids, "reembolso": reembolso, "refund_id": refund_id}


def _process_refund(engine: Engine, event: dict) -> str:
    return SqlPaymentsStore(engine, clock=lambda: NOW).process_refund(
        event["id"], event["type"], interpret_refund_event(event)
    )


def _estados(engine: Engine, ids: dict[str, str], event_id: str) -> tuple[str, str, str, str]:
    with engine.connect() as conn:
        reembolso = conn.execute(
            text("SELECT estado FROM reembolsos WHERE id = :id"), {"id": ids["reembolso"]}
        ).scalar_one()
        pago = conn.execute(
            text("SELECT estado FROM pagos WHERE id = :id"), {"id": ids["pago"]}
        ).scalar_one()
        factura = conn.execute(
            text("SELECT estado FROM facturas WHERE id = :id"), {"id": ids["factura"]}
        ).scalar_one()
        evento = conn.execute(
            text("SELECT estado FROM stripe_eventos WHERE stripe_event_id = :e"), {"e": event_id}
        ).scalar_one_or_none()
    return reembolso, pago, factura, evento


def test_succeeded_refund_marks_reembolso_processed(migrated_engine: Engine) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(ids["refund_id"], "succeeded")

    assert _process_refund(migrated_engine, event) == "PROCESADO"
    assert _estados(migrated_engine, ids, event["id"]) == (
        "PROCESADO",
        "CONFIRMADO",
        "REEMBOLSADA",
        "PROCESADO",
    )
    with migrated_engine.connect() as conn:
        procesado = conn.execute(
            text("SELECT procesado_en FROM reembolsos WHERE id = :id"),
            {"id": ids["reembolso"]},
        ).scalar_one()
    assert procesado is not None


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_failed_or_canceled_refund_marks_reembolso_failed_and_keeps_payment(
    migrated_engine: Engine, status: str
) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(ids["refund_id"], status)

    assert _process_refund(migrated_engine, event) == "PROCESADO"
    assert _estados(migrated_engine, ids, event["id"]) == (
        "FALLIDO",
        "CONFIRMADO",
        "PAGADA",
        "PROCESADO",
    )


@pytest.mark.parametrize("status", ["pending", "requires_action"])
def test_non_terminal_refund_status_changes_nothing(migrated_engine: Engine, status: str) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(ids["refund_id"], status)

    assert _process_refund(migrated_engine, event) == "IGNORADO"
    assert _estados(migrated_engine, ids, event["id"]) == (
        "PENDIENTE",
        "CONFIRMADO",
        "PAGADA",
        "IGNORADO",
    )


def test_unknown_refund_id_is_ignored_without_any_write(migrated_engine: Engine) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(f"re_unknown_{uuid.uuid4().hex}", "succeeded")

    assert _process_refund(migrated_engine, event) == "IGNORADO"
    assert _estados(migrated_engine, ids, event["id"]) == (
        "PENDIENTE",
        "CONFIRMADO",
        "PAGADA",
        None,
    )


def test_redelivered_refund_event_is_a_duplicate_and_changes_nothing(
    migrated_engine: Engine,
) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(ids["refund_id"], "succeeded")
    _process_refund(migrated_engine, event)

    assert _process_refund(migrated_engine, event) == "DUPLICADO"
    assert _estados(migrated_engine, ids, event["id"])[0] == "PROCESADO"


def test_new_event_cannot_move_a_reembolso_that_is_no_longer_pending(
    migrated_engine: Engine,
) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    _process_refund(migrated_engine, _refund_event(ids["refund_id"], "failed"))

    # A different event (different Stripe event id) must not move FALLIDO to PROCESADO.
    later = _refund_event(ids["refund_id"], "succeeded")
    assert _process_refund(migrated_engine, later) == "IGNORADO"
    assert _estados(migrated_engine, ids, later["id"])[0] == "FALLIDO"


def test_signed_refund_event_goes_through_the_webhook_service(migrated_engine: Engine) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    body = json.dumps(_refund_event(ids["refund_id"], "succeeded")).encode()
    timestamp = int(NOW.timestamp())
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    service = StripeWebhookService(
        store=SqlPaymentsStore(migrated_engine, clock=lambda: NOW),
        secret=SECRET,
        clock=lambda: timestamp,
    )

    assert service.handle(body, f"t={timestamp},v1={digest}") == "PROCESADO"
    assert _estados(migrated_engine, ids, json.loads(body)["id"])[0] == "PROCESADO"

"""Stripe event interpretation and payment decisions (E4). Pure domain, no I/O."""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.payments.domain.webhook import (
    CANCELADO,
    CONFIRMADO,
    FALLIDO,
    IGNORADO,
    PENDIENTE,
    PROCESADO,
    RECHAZADO,
    PagoSnapshot,
    decide,
    interpret_event,
)


def _session(event_type: str, **fields: object) -> dict:
    session = {
        "id": "cs_test_synthetic_1",
        "payment_status": "paid",
        "amount_total": 5000,
        "currency": "usd",
    }
    session.update(fields)
    return {"id": "evt_test_1", "type": event_type, "data": {"object": session}}


def _pago(**overrides: object) -> PagoSnapshot:
    values = {
        "pago_id": "11111111-1111-4111-8111-111111111111",
        "factura_id": "22222222-2222-4222-8222-222222222222",
        "estado": PENDIENTE,
        "monto": Decimal("50.00"),
        "moneda": "USD",
        "factura_estado": "PENDIENTE",
    }
    values.update(overrides)
    return PagoSnapshot(**values)  # type: ignore[arg-type]


def test_paid_checkout_completed_confirms_with_amount_and_currency() -> None:
    change = interpret_event(_session("checkout.session.completed"))
    assert change.target_status == CONFIRMADO
    assert change.session_id == "cs_test_synthetic_1"
    assert change.amount_cents == 5000
    assert change.currency == "USD"


def test_unpaid_checkout_completed_changes_nothing() -> None:
    change = interpret_event(_session("checkout.session.completed", payment_status="unpaid"))
    assert change.target_status is None


def test_async_payment_succeeded_confirms() -> None:
    change = interpret_event(_session("checkout.session.async_payment_succeeded"))
    assert change.target_status == CONFIRMADO


def test_async_payment_failed_rejects() -> None:
    change = interpret_event(_session("checkout.session.async_payment_failed"))
    assert change.target_status == RECHAZADO


def test_expired_session_cancels() -> None:
    change = interpret_event(_session("checkout.session.expired"))
    assert change.target_status == CANCELADO


def test_unhandled_event_type_changes_nothing() -> None:
    change = interpret_event(_session("charge.refunded"))
    assert change.target_status is None


def test_event_without_pago_is_ignored() -> None:
    change = interpret_event(_session("checkout.session.completed"))
    decision = decide(change, None)
    assert decision.outcome == IGNORADO


def test_unhandled_event_is_ignored_even_with_a_pago() -> None:
    change = interpret_event(_session("charge.refunded"))
    assert decide(change, _pago()).outcome == IGNORADO


def test_pago_already_resolved_is_ignored() -> None:
    change = interpret_event(_session("checkout.session.completed"))
    decision = decide(change, _pago(estado=CONFIRMADO))
    assert decision.outcome == IGNORADO


def test_matching_payment_is_confirmed() -> None:
    change = interpret_event(_session("checkout.session.completed"))
    decision = decide(change, _pago())
    assert decision.outcome == PROCESADO
    assert decision.target_status == CONFIRMADO


def test_amount_mismatch_fails_without_confirming() -> None:
    change = interpret_event(_session("checkout.session.completed", amount_total=4999))
    decision = decide(change, _pago())
    assert decision.outcome == FALLIDO
    assert decision.target_status is None


def test_currency_mismatch_fails_without_confirming() -> None:
    change = interpret_event(_session("checkout.session.completed", currency="eur"))
    decision = decide(change, _pago())
    assert decision.outcome == FALLIDO


def test_confirmation_on_a_non_pending_invoice_fails() -> None:
    change = interpret_event(_session("checkout.session.completed"))
    decision = decide(change, _pago(factura_estado="ANULADA"))
    assert decision.outcome == FALLIDO


def test_rejection_does_not_check_amount() -> None:
    change = interpret_event(_session("checkout.session.async_payment_failed", amount_total=1))
    decision = decide(change, _pago())
    assert decision.outcome == PROCESADO
    assert decision.target_status == RECHAZADO

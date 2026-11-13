"""Stripe webhook rules (E4): signature, event interpretation and payment decisions.

Pure functions. The signature check follows Stripe's documented scheme: HMAC-SHA256 over
"<timestamp>.<raw body>", the ``v1`` scheme only, and a 5 minute tolerance against replay.
Payments are confirmed only by a verified event, never by a browser redirect.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, replace
from decimal import Decimal

from medicalbooking.modules.payments.domain.refunds import REFUND_STATUS_OUTCOME
from medicalbooking.shared.domain.errors import AppError

TOLERANCE_SECONDS = 300
SIGNATURE_SCHEME = "v1"

PENDIENTE = "PENDIENTE"
CONFIRMADO = "CONFIRMADO"
RECHAZADO = "RECHAZADO"
CANCELADO = "CANCELADO"

PROCESADO = "PROCESADO"
IGNORADO = "IGNORADO"
FALLIDO = "FALLIDO"
DUPLICADO = "DUPLICADO"

_FACTURA_PENDIENTE = "PENDIENTE"


class InvalidSignature(AppError):
    """The Stripe-Signature header is missing, malformed, stale or does not match."""

    status = 400
    title = "Bad Request"


@dataclass(frozen=True)
class PaymentChange:
    """What a Stripe event asks for: a target payment state, or nothing."""

    session_id: str | None
    target_status: str | None
    amount_cents: int | None
    currency: str | None
    # Provider payment intent id, set only by checkout.session.completed. Stored, never emitted.
    payment_intent: str | None = None


@dataclass(frozen=True)
class RefundChange:
    """What a Stripe refund event asks for: a reembolso outcome for one Stripe refund id."""

    refund_id: str | None
    target_status: str | None


@dataclass(frozen=True)
class PagoSnapshot:
    pago_id: str
    factura_id: str
    estado: str
    monto: Decimal
    moneda: str
    factura_estado: str


@dataclass(frozen=True)
class Decision:
    outcome: str
    target_status: str | None = None
    detail: str | None = None


def verify_signature(
    payload: bytes, header: str | None, secret: str, now: int, tolerance: int = TOLERANCE_SECONDS
) -> None:
    """Raise InvalidSignature unless the header is a fresh, valid v1 signature of the payload."""
    if not header:
        raise InvalidSignature("missing Stripe-Signature header")
    timestamp, signatures = _parse_header(header)
    if abs(now - timestamp) > tolerance:
        raise InvalidSignature("timestamp outside tolerance")
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    # Compare bytes: compare_digest raises TypeError on non-ASCII str, which a client can send.
    expected_bytes = expected.encode("ascii")
    if not any(
        hmac.compare_digest(expected_bytes, candidate.encode("utf-8")) for candidate in signatures
    ):
        raise InvalidSignature("no matching v1 signature")


_CANONICAL_TIMESTAMP = re.compile(r"[0-9]{1,18}")


def _parse_header(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for element in header.split(","):
        prefix, _, value = element.strip().partition("=")
        if prefix == "t":
            # Canonical decimal digits only: int() would also accept signs, underscores, spaces.
            if not _CANONICAL_TIMESTAMP.fullmatch(value):
                raise InvalidSignature("timestamp is not an integer")
            timestamp = int(value)
        elif prefix == SIGNATURE_SCHEME and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise InvalidSignature("header lacks a timestamp or a v1 signature")
    return timestamp, signatures


_TARGET_BY_EVENT = {
    "checkout.session.async_payment_succeeded": CONFIRMADO,
    "checkout.session.async_payment_failed": RECHAZADO,
    "checkout.session.expired": CANCELADO,
}


def interpret_event(event: dict) -> PaymentChange:
    """Map a verified Stripe event to the payment change it requests, if any."""
    event_type = event.get("type", "")
    session = (event.get("data") or {}).get("object") or {}
    session_id = session.get("id")
    if event_type == "checkout.session.completed":
        # The intent exists once the session completes, paid or not (async methods). It is
        # recorded for the payment of this session, and the paid check decides the state only.
        intent = _payment_intent(session)
        if session.get("payment_status") == "paid":
            return replace(_confirmation(session_id, session), payment_intent=intent)
        return PaymentChange(session_id, None, None, None, payment_intent=intent)
    target = _TARGET_BY_EVENT.get(event_type)
    if target == CONFIRMADO:
        return _confirmation(session_id, session)
    return PaymentChange(session_id, target, None, None)


def _payment_intent(session: dict) -> str | None:
    """Only a plain id string is kept: an expanded object or a null field is not stored."""
    value = session.get("payment_intent")
    return value if isinstance(value, str) and value else None



def refund_target_status(status: object) -> str | None:
    """Map a Stripe refund status to the reembolso outcome; None means no change."""
    if not isinstance(status, str):
        return None
    return REFUND_STATUS_OUTCOME.get(status)


def interpret_refund_event(event: dict) -> RefundChange:
    """Map a verified refund.* event to the reembolso change it requests, if any."""
    data = event.get("data")
    refund = data.get("object") if isinstance(data, dict) else None
    if not isinstance(refund, dict):
        return RefundChange(refund_id=None, target_status=None)
    refund_id = refund.get("id")
    return RefundChange(
        refund_id=refund_id if isinstance(refund_id, str) else None,
        target_status=refund_target_status(refund.get("status")),
    )


def _confirmation(session_id: str | None, session: dict) -> PaymentChange:
    amount = session.get("amount_total")
    currency = session.get("currency")
    return PaymentChange(
        session_id=session_id,
        target_status=CONFIRMADO,
        amount_cents=amount if isinstance(amount, int) else None,
        currency=currency.upper() if isinstance(currency, str) else None,
    )


def decide(change: PaymentChange, pago: PagoSnapshot | None) -> Decision:
    """Decide the outcome for one event against the stored payment, if one matches."""
    if change.target_status is None:
        return Decision(IGNORADO, detail="evento sin cambio de pago")
    if pago is None:
        return Decision(IGNORADO, detail="sin pago asociado a la sesion")
    if pago.estado != PENDIENTE:
        return Decision(IGNORADO, detail=f"pago ya en estado {pago.estado}")
    if change.target_status == CONFIRMADO:
        mismatch = _confirmation_mismatch(change, pago)
        if mismatch is not None:
            return Decision(FALLIDO, detail=mismatch)
    return Decision(PROCESADO, target_status=change.target_status)


def _confirmation_mismatch(change: PaymentChange, pago: PagoSnapshot) -> str | None:
    if pago.factura_estado != _FACTURA_PENDIENTE:
        return "factura no pendiente"
    if change.amount_cents != int(pago.monto * 100):
        return "monto no coincide"
    if change.currency != pago.moneda.upper():
        return "moneda no coincide"
    return None

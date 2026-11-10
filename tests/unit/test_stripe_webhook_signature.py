"""Stripe webhook signature verification (E4). Pure, no network, no database."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from medicalbooking.modules.payments.domain.webhook import InvalidSignature, verify_signature

SECRET = "whsec_test_synthetic_secret"
PAYLOAD = b'{"id":"evt_test_1","type":"checkout.session.completed"}'
NOW = 1_800_000_000


def _sign(payload: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def _header(payload: bytes = PAYLOAD, secret: str = SECRET, timestamp: int = NOW) -> str:
    return f"t={timestamp},v1={_sign(payload, secret, timestamp)}"


def test_valid_signature_is_accepted() -> None:
    verify_signature(PAYLOAD, _header(), SECRET, now=NOW)


def test_signature_made_with_another_secret_is_rejected() -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, _header(secret="whsec_other"), SECRET, now=NOW)


def test_tampered_payload_is_rejected() -> None:
    header = _header()
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD + b" ", header, SECRET, now=NOW)


def test_timestamp_older_than_tolerance_is_rejected() -> None:
    old = NOW - 301
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, _header(timestamp=old), SECRET, now=NOW)


def test_timestamp_exactly_at_tolerance_is_accepted() -> None:
    verify_signature(PAYLOAD, _header(timestamp=NOW - 300), SECRET, now=NOW)


def test_timestamp_too_far_in_the_future_is_rejected() -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, _header(timestamp=NOW + 301), SECRET, now=NOW)


def test_missing_header_is_rejected() -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, None, SECRET, now=NOW)


@pytest.mark.parametrize(
    "header",
    ["", "v1=abc", f"t=not-a-number,v1={_sign(PAYLOAD, SECRET, NOW)}", f"t={NOW}"],
)
def test_malformed_header_is_rejected(header: str) -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, header, SECRET, now=NOW)


@pytest.mark.parametrize("timestamp", ["+1800000000", "1_800_000_000", " 1800000000", "1e9"])
def test_non_canonical_timestamp_is_rejected(timestamp: str) -> None:
    header = f"t={timestamp},v1={_sign(PAYLOAD, SECRET, NOW)}"
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, header, SECRET, now=NOW)


def test_non_ascii_signature_value_is_rejected_without_error() -> None:
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, f"t={NOW},v1=\u00e9\u00e8", SECRET, now=NOW)


def test_only_v0_scheme_is_rejected() -> None:
    header = f"t={NOW},v0={_sign(PAYLOAD, SECRET, NOW)}"
    with pytest.raises(InvalidSignature):
        verify_signature(PAYLOAD, header, SECRET, now=NOW)


def test_valid_v1_is_accepted_when_other_schemes_are_present() -> None:
    header = f"t={NOW},v0=deadbeef,v1={_sign(PAYLOAD, SECRET, NOW)}"
    verify_signature(PAYLOAD, header, SECRET, now=NOW)


def test_one_valid_v1_among_several_is_accepted() -> None:
    header = f"t={NOW},v1=deadbeef,v1={_sign(PAYLOAD, SECRET, NOW)}"
    verify_signature(PAYLOAD, header, SECRET, now=NOW)

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from medicalbooking.modules.payments.domain.invoice_links import (
    INVOICE_LINK_TTL,
    invoice_download_url,
    sign_invoice_link,
    verify_invoice_link,
)

KEY = "k" * 40
PAGO = "6f1c2a4e-0000-4000-8000-000000000001"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def test_a_fresh_link_verifies_for_its_own_payment() -> None:
    link = sign_invoice_link(KEY, PAGO, NOW)

    assert verify_invoice_link(KEY, PAGO, link.expires, link.signature, NOW)


def test_the_link_lasts_the_ttl_and_then_expires() -> None:
    link = sign_invoice_link(KEY, PAGO, NOW)
    just_before = NOW + INVOICE_LINK_TTL - timedelta(seconds=1)
    at_expiry = NOW + INVOICE_LINK_TTL

    assert verify_invoice_link(KEY, PAGO, link.expires, link.signature, just_before)
    assert not verify_invoice_link(KEY, PAGO, link.expires, link.signature, at_expiry)


def test_a_link_does_not_open_another_payment() -> None:
    link = sign_invoice_link(KEY, PAGO, NOW)
    other = "6f1c2a4e-0000-4000-8000-000000000002"

    assert not verify_invoice_link(KEY, other, link.expires, link.signature, NOW)


def test_a_changed_expiry_is_refused() -> None:
    link = sign_invoice_link(KEY, PAGO, NOW)

    assert not verify_invoice_link(KEY, PAGO, link.expires + 3600, link.signature, NOW)


def test_a_link_signed_with_another_key_is_refused() -> None:
    link = sign_invoice_link("x" * 40, PAGO, NOW)

    assert not verify_invoice_link(KEY, PAGO, link.expires, link.signature, NOW)


def test_a_tampered_signature_is_refused() -> None:
    link = sign_invoice_link(KEY, PAGO, NOW)
    forged = ("0" if link.signature[0] != "0" else "1") + link.signature[1:]

    assert not verify_invoice_link(KEY, PAGO, link.expires, forged, NOW)
    assert not verify_invoice_link(KEY, PAGO, link.expires, "", NOW)


def test_download_url_points_to_the_signed_endpoint() -> None:
    url = invoice_download_url("https://api.example/", KEY, PAGO, NOW)

    link = sign_invoice_link(KEY, PAGO, NOW)
    assert url == (
        f"https://api.example/api/v1/payments/{PAGO}/factura.pdf"
        f"?exp={link.expires}&sig={link.signature}"
    )

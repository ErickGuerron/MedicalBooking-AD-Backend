"""Signed, expiring links to a paid invoice's PDF, sent in the payment confirmation email.

The link carries the payment id, an expiry instant and an HMAC-SHA256 over both, keyed with
the signing key. Possession of a valid, unexpired link is the access; nothing else is checked.
The signature uses its own context label so it can never be confused with another HMAC use of
the same key.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

INVOICE_LINK_TTL = timedelta(days=7)
_CONTEXT = b"medilo.invoice-pdf.v1"


@dataclass(frozen=True)
class SignedInvoiceLink:
    expires: int
    signature: str


def _mac(key: str, pago_id: str, expires: int) -> str:
    message = _CONTEXT + b"\n" + f"{pago_id}\n{expires}".encode()
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_invoice_link(key: str, pago_id: str, now: datetime) -> SignedInvoiceLink:
    """Signs access to one payment's invoice until the TTL from `now` has passed."""
    expires = int((now + INVOICE_LINK_TTL).timestamp())
    return SignedInvoiceLink(expires=expires, signature=_mac(key, pago_id, expires))


def verify_invoice_link(
    key: str, pago_id: str, expires: int, signature: str, now: datetime
) -> bool:
    """True only for an untampered link that has not expired yet."""
    if now.timestamp() >= expires:
        return False
    return hmac.compare_digest(_mac(key, pago_id, expires), signature)


def invoice_download_url(base_url: str, key: str, pago_id: str, now: datetime) -> str:
    """Absolute URL of the signed PDF endpoint for one payment."""
    link = sign_invoice_link(key, pago_id, now)
    base = base_url.rstrip("/")
    return f"{base}/api/v1/payments/{pago_id}/factura.pdf?exp={link.expires}&sig={link.signature}"

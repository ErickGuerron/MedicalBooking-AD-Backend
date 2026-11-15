from __future__ import annotations

import pytest

from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import PAGO_COMPLETADO
from medicalbooking.modules.notifications.infrastructure.email_render import (
    content_for,
    payment_content,
)

DOWNLOAD = "https://api.example/api/v1/payments/p1/factura.pdf?exp=1&sig=abc"


def test_payment_email_greets_the_patient_and_names_the_invoice() -> None:
    content = payment_content("Ana", "FAC-000123", "25.00 USD", "https://assets.example", DOWNLOAD)

    assert content.subject == "Recibimos tu pago en Medilo"
    assert "Hola Ana" in content.text
    assert "FAC-000123" in content.text
    assert "25.00 USD" in content.text
    assert content.html is not None
    assert "FAC-000123" in content.html
    assert "25.00 USD" in content.html


def test_payment_email_links_the_signed_invoice_download() -> None:
    content = payment_content("Ana", "FAC-1", "1.00 USD", "https://assets.example", DOWNLOAD)

    assert content.html is not None
    assert 'href="https://api.example/api/v1/payments/p1/factura.pdf?exp=1&amp;sig=abc"' in (
        content.html
    )
    assert DOWNLOAD in content.text


def test_payment_email_escapes_the_patient_name_in_html() -> None:
    content = payment_content(
        "<script>x</script>", "FAC-1", "1.00 USD", "https://assets.example", DOWNLOAD
    )

    assert content.html is not None
    assert "<script>x</script>" not in content.html
    assert "&lt;script&gt;" in content.html


class _Store:
    def patient_first_name(self, email: str) -> str:
        return "Ana"

    def paid_invoice(self, pago_id: str) -> tuple[str, str]:
        return ("FAC-1", "1.00 USD")


def test_payment_email_is_refused_without_an_invoice_link_factory() -> None:
    notification = DueNotification(
        id="n1",
        tipo=PAGO_COMPLETADO,
        destinatario="ana@example.com",
        numero_intentos=0,
        aggregate_id="p1",
    )

    with pytest.raises(NotificationError, match="INVOICE_LINK_NOT_CONFIGURED"):
        content_for(notification, _Store(), "https://assets.example")


def test_payment_email_uses_the_invoice_link_factory_for_its_payment() -> None:
    notification = DueNotification(
        id="n1",
        tipo=PAGO_COMPLETADO,
        destinatario="ana@example.com",
        numero_intentos=0,
        aggregate_id="p1",
    )

    content = content_for(
        notification,
        _Store(),
        "https://assets.example",
        invoice_download_url=lambda pago_id: f"https://api.example/pagos/{pago_id}.pdf",
    )

    assert content.html is not None
    assert 'href="https://api.example/pagos/p1.pdf"' in content.html

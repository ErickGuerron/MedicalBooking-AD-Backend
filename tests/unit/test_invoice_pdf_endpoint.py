from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from flask import Flask

from medicalbooking.modules.payments.application.invoice_report import (
    InvoiceDocument,
    InvoiceLine,
)
from medicalbooking.modules.payments.domain.invoice_links import (
    INVOICE_LINK_TTL,
    sign_invoice_link,
)
from medicalbooking.modules.payments.presentation import routes
from medicalbooking.shared.domain.errors import AppError

KEY = "k" * 40
PAGO = "6f1c2a4e-0000-4000-8000-000000000001"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


class _FakeInvoices:
    found = True

    def __init__(self, engine) -> None:
        self._engine = engine

    def paid_invoice_for_payment(self, pago_id: str) -> InvoiceDocument | None:
        if not self.found:
            return None
        return InvoiceDocument(
            numero_factura="FAC-000123",
            emitida_en=NOW,
            pagada_en=NOW,
            paciente_nombre="Ana Pérez",
            documento="CI 1712345678",
            moneda="USD",
            lineas=(
                InvoiceLine(
                    descripcion="Consulta medicina general",
                    cantidad=1,
                    precio_unitario=Decimal("25.00"),
                    subtotal=Decimal("25.00"),
                ),
            ),
            subtotal=Decimal("25.00"),
            impuestos=Decimal("0.00"),
            total=Decimal("25.00"),
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "SqlInvoiceStore", _FakeInvoices)
    app = Flask(__name__)
    app.register_blueprint(routes.payments_bp)

    @app.errorhandler(AppError)
    def _app_error(error: AppError):
        return "", error.status

    app.extensions["medicalbooking.engine"] = object()
    app.extensions[routes.INVOICE_LINK_EXTENSION] = KEY
    return app.test_client()


def _path(pago: str = PAGO, expires: int | None = None, signature: str | None = None) -> str:
    link = sign_invoice_link(KEY, PAGO, NOW)
    exp = link.expires if expires is None else expires
    sig = link.signature if signature is None else signature
    return f"/api/v1/payments/{pago}/factura.pdf?exp={exp}&sig={sig}"


def test_a_signed_link_returns_the_invoice_pdf(client, monkeypatch) -> None:
    monkeypatch.setattr(routes, "datetime", _FixedClock(NOW))
    response = client.get(_path())

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")
    assert 'filename="factura-FAC-000123.pdf"' in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "private, no-store"


def test_a_tampered_signature_is_refused(client, monkeypatch) -> None:
    monkeypatch.setattr(routes, "datetime", _FixedClock(NOW))
    response = client.get(_path(signature="0" * 64))

    assert response.status_code == 404


def test_an_expired_link_is_refused(client, monkeypatch) -> None:
    after_expiry = NOW + INVOICE_LINK_TTL + timedelta(seconds=1)
    monkeypatch.setattr(routes, "datetime", _FixedClock(after_expiry))
    response = client.get(_path())

    assert response.status_code == 404


def test_a_link_for_another_payment_is_refused(client, monkeypatch) -> None:
    monkeypatch.setattr(routes, "datetime", _FixedClock(NOW))
    other = "6f1c2a4e-0000-4000-8000-000000000002"
    response = client.get(_path(pago=other))

    assert response.status_code == 404


def test_a_missing_or_malformed_expiry_is_refused(client) -> None:
    assert client.get(f"/api/v1/payments/{PAGO}/factura.pdf?sig=abc").status_code == 404
    assert client.get(f"/api/v1/payments/{PAGO}/factura.pdf?exp=x&sig=abc").status_code == 404


def test_an_unknown_payment_answers_the_same_404(client, monkeypatch) -> None:
    monkeypatch.setattr(routes, "datetime", _FixedClock(NOW))
    monkeypatch.setattr(_FakeInvoices, "found", False)
    response = client.get(_path())

    assert response.status_code == 404


class _FixedClock:
    """Stands in for the route's datetime: only `now` is used there."""

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None) -> datetime:
        return self._moment

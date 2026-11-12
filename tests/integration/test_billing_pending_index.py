"""One pending checkout attempt per invoice, enforced by SQL Server (migration 0019)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from tests.integration.checkout_support import new_id as _id

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _payment(factura: str, clave: str) -> NewPayment:
    return NewPayment(
        id=_id(),
        factura_id=factura,
        clave=clave,
        monto=Decimal("25.50"),
        moneda="USD",
        creado_en=NOW,
    )


def _estados(engine: Engine, factura: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT estado FROM pagos WHERE factura_id = :f"), {"f": factura}
        ).all()
    return [row.estado for row in rows]


def test_the_index_exists_and_filters_on_pending(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT filter_definition FROM sys.indexes "
                "WHERE name = N'UX_pagos_factura_pendiente' "
                "AND object_id = OBJECT_ID(N'dbo.pagos') AND is_unique = 1"
            )
        ).first()
    assert row is not None
    assert "PENDIENTE" in row.filter_definition


def test_a_second_pending_attempt_for_the_same_invoice_is_refused_by_the_database(
    engine: Engine, factura: str
) -> None:
    store = SqlBillingStore(engine)
    store.reserve_payment(_payment(factura, "db-key-0000001"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pagos (id, factura_id, proveedor, clave_idempotencia, monto, "
                    "moneda, estado, creado_en) VALUES (:id, :f, 'STRIPE', :k, 25.50, 'USD', "
                    "'PENDIENTE', SYSUTCDATETIME())"
                ),
                {"id": _id(), "f": factura, "k": "db-key-0000002"},
            )


def test_a_losing_insert_returns_the_committed_attempt_instead_of_failing(
    engine: Engine, factura: str
) -> None:
    store = SqlBillingStore(engine)
    winner = store.reserve_payment(_payment(factura, "win-key-0000001")).payment
    loser = store.reserve_payment(_payment(factura, "lose-key-000002"))
    assert loser.created is False
    assert loser.payment.id == winner.id
    assert _estados(engine, factura) == ["PENDIENTE"]


def test_a_rejected_attempt_does_not_block_a_new_one(engine: Engine, factura: str) -> None:
    store = SqlBillingStore(engine)
    first = store.reserve_payment(_payment(factura, "first-key-000001")).payment
    store.mark_rejected(first.id)
    second = store.reserve_payment(_payment(factura, "second-key-00002"))
    assert second.created is True
    assert sorted(_estados(engine, factura)) == ["PENDIENTE", "RECHAZADO"]


def test_pending_lookup_returns_the_provider_session(engine: Engine, factura: str) -> None:
    store = SqlBillingStore(engine)
    created = store.reserve_payment(_payment(factura, "sess-key-0000001")).payment
    store.record_session(created.id, "cs_test_sess_1", 0)
    found = store.find_pending_payment_for_invoice(factura)
    assert found is not None
    assert found.session_id == "cs_test_sess_1"


def test_confirm_payment_settles_the_attempt_and_the_invoice_once(
    engine: Engine, factura: str
) -> None:
    store = SqlBillingStore(engine)
    payment = store.reserve_payment(_payment(factura, "paid-key-0000001")).payment
    store.confirm_payment(payment.id, factura, NOW)
    store.confirm_payment(payment.id, factura, NOW)  # a repeat changes nothing and fails nothing
    assert _estados(engine, factura) == ["CONFIRMADO"]
    with engine.connect() as conn:
        factura_estado = conn.execute(
            text("SELECT estado FROM facturas WHERE id = :f"), {"f": factura}
        ).scalar_one()
    assert factura_estado == "PAGADA"


def test_cancelled_attempt_frees_the_invoice_for_a_new_one(engine: Engine, factura: str) -> None:
    store = SqlBillingStore(engine)
    old = store.reserve_payment(_payment(factura, "gone-key-0000001")).payment
    store.cancel_pending_payment(old.id)
    assert store.find_pending_payment_for_invoice(factura) is None
    new = store.reserve_payment(_payment(factura, "next-key-0000002"))
    assert new.created is True
    assert sorted(_estados(engine, factura)) == ["CANCELADO", "PENDIENTE"]

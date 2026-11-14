"""Retry state on payment attempts (migration 0021, checkout-retry T1)."""

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


def test_a_new_attempt_starts_with_no_retries_and_no_schedule(engine: Engine, factura: str) -> None:
    payment = (
        SqlBillingStore(engine)
        .reserve_payment(
            NewPayment(
                id=_id(),
                factura_id=factura,
                clave="retry-default-0001",
                monto=Decimal("25.50"),
                moneda="USD",
                creado_en=NOW,
            )
        )
        .payment
    )
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT reintentos_realizados, reintentos_agotados, ultimo_error_codigo, "
                "proximo_reintento_en FROM pagos WHERE id = :id"
            ),
            {"id": payment.id},
        ).one()
    assert row.reintentos_realizados == 0
    assert row.reintentos_agotados is False
    assert row.ultimo_error_codigo is None
    assert row.proximo_reintento_en is None


def test_the_database_refuses_more_than_five_retries(engine: Engine, factura: str) -> None:
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO pagos (id, factura_id, proveedor, clave_idempotencia, monto, "
                    "moneda, estado, creado_en, reintentos_realizados) VALUES (:id, :f, 'STRIPE', "
                    ":k, 25.50, 'USD', 'PENDIENTE', SYSUTCDATETIME(), 6)"
                ),
                {"id": _id(), "f": factura, "k": "retry-check-00001"},
            )


def test_the_worker_index_covers_pending_attempts_with_a_due_retry(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT filter_definition FROM sys.indexes "
                "WHERE name = N'IX_pagos_reintento_pendiente' "
                "AND object_id = OBJECT_ID(N'dbo.pagos')"
            )
        ).first()
    assert row is not None
    assert "PENDIENTE" in row.filter_definition

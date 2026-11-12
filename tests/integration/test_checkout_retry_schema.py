"""Retry state on payment attempts (migration 0021, checkout-retry T1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _id() -> str:
    return str(uuid.uuid4())


def _seed_factura(conn: Connection) -> str:
    usuario, paciente, factura = _id(), _id(), _id()
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
            "creada_en) VALUES (:id, :p, 25.50, 0, 25.50, 'USD', 'PENDIENTE', SYSUTCDATETIME())"
        ),
        {"id": factura, "p": paciente},
    )
    return factura


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    return migrated_engine


@pytest.fixture
def factura(engine: Engine) -> str:
    with engine.begin() as conn:
        return _seed_factura(conn)


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

"""Checkout retry state written by SqlBillingStore (checkout-retry T2, migration 0021)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.domain.checkout_retry import CHECKOUT_UNAVAILABLE_CODE
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
def store(migrated_engine: Engine) -> SqlBillingStore:
    return SqlBillingStore(migrated_engine)


@pytest.fixture
def attempt(migrated_engine: Engine, store: SqlBillingStore) -> str:
    with migrated_engine.begin() as conn:
        factura = _seed_factura(conn)
    payment = store.reserve_payment(
        NewPayment(
            id=_id(),
            factura_id=factura,
            clave=f"store-key-{uuid.uuid4().hex[:12]}",
            monto=Decimal("25.50"),
            moneda="USD",
            creado_en=NOW,
        )
    ).payment
    return payment.id


def _row(engine: Engine, pago_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT estado, reintentos_realizados, reintentos_agotados, ultimo_error_codigo, "
                "proximo_reintento_en, stripe_checkout_session_id FROM pagos WHERE id = :id"
            ),
            {"id": pago_id},
        ).one()


def test_a_failed_create_keeps_the_attempt_pending_with_the_next_retry_recorded(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    due = NOW + timedelta(minutes=1)
    store.record_create_failure(attempt, 0, due, CHECKOUT_UNAVAILABLE_CODE)
    row = _row(migrated_engine, attempt)
    assert row.estado == "PENDIENTE"
    assert row.reintentos_realizados == 0
    assert row.ultimo_error_codigo == CHECKOUT_UNAVAILABLE_CODE
    assert row.proximo_reintento_en == due.replace(tzinfo=None)


def test_an_exhausted_attempt_is_rejected_and_frees_the_invoice(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    store.record_create_failure(attempt, 5, None, CHECKOUT_UNAVAILABLE_CODE)
    row = _row(migrated_engine, attempt)
    assert row.estado == "RECHAZADO"
    assert row.reintentos_agotados is True
    assert row.proximo_reintento_en is None
    assert store.find_pending_payment_for_invoice(_factura_of(migrated_engine, attempt)) is None


def test_a_session_recorded_after_a_retry_clears_the_schedule_and_counts_the_retry(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    store.record_create_failure(attempt, 0, NOW + timedelta(minutes=1), CHECKOUT_UNAVAILABLE_CODE)
    store.record_session(attempt, "cs_test_retry_ok", 1)
    row = _row(migrated_engine, attempt)
    assert row.estado == "PENDIENTE"
    assert row.stripe_checkout_session_id == "cs_test_retry_ok"
    assert row.reintentos_realizados == 1
    assert row.proximo_reintento_en is None


def test_a_late_failure_never_touches_an_attempt_that_already_has_a_session(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    store.record_session(attempt, "cs_test_first", 0)
    store.record_create_failure(attempt, 1, NOW + timedelta(minutes=1), CHECKOUT_UNAVAILABLE_CODE)
    row = _row(migrated_engine, attempt)
    assert row.stripe_checkout_session_id == "cs_test_first"
    assert row.reintentos_realizados == 0
    assert row.proximo_reintento_en is None


def _factura_of(engine: Engine, pago_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT factura_id FROM pagos WHERE id = :id"), {"id": pago_id}
            ).scalar_one()
        ).lower()

"""Checkout retry state written by SqlBillingStore (checkout-retry T2, migration 0021)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.domain.checkout_retry import CHECKOUT_UNAVAILABLE_CODE
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from tests.integration.checkout_support import new_id as _id
from tests.integration.checkout_support import seed_factura as _seed_factura

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


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


def test_only_attempts_whose_retry_is_due_are_listed_for_the_worker(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    # The database is shared across tests, so only this test's attempt is asserted on.
    store.record_create_failure(attempt, 0, NOW + timedelta(minutes=1), CHECKOUT_UNAVAILABLE_CODE)
    assert attempt not in [p.id for p in store.find_due_checkout_retries(NOW, 500)]
    due = {p.id: p for p in store.find_due_checkout_retries(NOW + timedelta(minutes=2), 500)}
    assert attempt in due
    assert due[attempt].proximo_reintento_en == (NOW + timedelta(minutes=1))


def test_exhausted_or_answered_attempts_are_never_listed_for_retry(
    migrated_engine: Engine, store: SqlBillingStore, attempt: str
) -> None:
    store.record_create_failure(attempt, 5, None, CHECKOUT_UNAVAILABLE_CODE)
    assert attempt not in [
        p.id for p in store.find_due_checkout_retries(NOW + timedelta(days=1), 500)
    ]

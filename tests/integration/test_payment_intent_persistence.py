"""Stripe payment_intent id persisted on a payment (refund prerequisite), against SQL Server."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.billing_service import NewPayment
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from tests.integration.checkout_support import new_id as _id
from tests.integration.checkout_support import seed_factura as _seed_factura

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def intent() -> str:
    # The database is shared and the store commits, so each test needs its own intent id:
    # the filtered unique index rejects the same id on two payments.
    return f"pi_test_{uuid.uuid4().hex[:16]}"


@pytest.fixture
def store(migrated_engine: Engine) -> SqlBillingStore:
    return SqlBillingStore(migrated_engine)


@pytest.fixture
def pago(migrated_engine: Engine, store: SqlBillingStore) -> str:
    with migrated_engine.begin() as conn:
        factura = _seed_factura(conn)
    return store.reserve_payment(
        NewPayment(
            id=_id(),
            factura_id=factura,
            clave=f"intent-key-{uuid.uuid4().hex[:12]}",
            monto=Decimal("25.50"),
            moneda="USD",
            creado_en=NOW,
        )
    ).payment.id


def _intent(engine: Engine, pago_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT stripe_payment_intent_id FROM pagos WHERE id = :id"), {"id": pago_id}
        ).scalar_one()


def _outbox_mentions(engine: Engine, needle: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT COUNT(*) FROM eventos_outbox "
                "WHERE CAST(payload AS NVARCHAR(MAX)) LIKE :needle"
            ),
            {"needle": f"%{needle}%"},
        ).scalar_one()


def test_the_first_intent_is_stored_on_the_payment(
    migrated_engine: Engine, store: SqlBillingStore, pago: str, intent: str
) -> None:
    assert _intent(migrated_engine, pago) is None

    store.record_payment_intent(pago, intent)

    assert _intent(migrated_engine, pago) == intent


def test_a_recorded_intent_is_never_overwritten_by_a_repeat(
    migrated_engine: Engine, store: SqlBillingStore, pago: str, intent: str
) -> None:
    store.record_payment_intent(pago, intent)

    store.record_payment_intent(pago, f"{intent}_other")

    assert _intent(migrated_engine, pago) == intent


def test_recording_the_intent_writes_no_outbox_event_and_leaks_nothing_into_one(
    migrated_engine: Engine, store: SqlBillingStore, pago: str, intent: str
) -> None:
    before = _outbox_mentions(migrated_engine, intent)

    store.record_payment_intent(pago, intent)

    assert _outbox_mentions(migrated_engine, intent) == before == 0

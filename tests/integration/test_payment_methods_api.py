"""Saved payment methods against real SQL Server: migration 0020, the customer mapping and the
/me/payment-methods routes with a stubbed Stripe gateway (no network calls).

Each test builds its own users with unique identifiers.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.payments.application.billing_service import (
    BillingService,
    CheckoutRequest,
    CheckoutSession,
)
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.application.payment_methods import (
    CardSnapshot,
    PaymentMethodService,
    PaymentMethodSnapshot,
)
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.customer_repositories import (
    SqlStripeCustomerStore,
)
from tests.integration.test_billing_api import _booked_appointment
from tests.integration.test_scheduling_api import _count, _token, _user

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PM_CARD = "pm_test_card_integration_1"
PM_OTHER = "pm_test_card_integration_2"


class StubStripe:
    """Stands in for Stripe for the saved-card and checkout routes. Records calls, no network."""

    publishable_key = "pk_test_stub_publishable"

    def __init__(self) -> None:
        self.customers_created: list[str] = []
        self.owners: dict[str, str | None] = {}
        self.cards: dict[str, list[CardSnapshot]] = {}
        self.detached: list[str] = []
        self.checkout_requests: list[CheckoutRequest] = []

    def is_configured(self) -> bool:
        return True

    def create_customer(self, idempotency_key: str) -> str:
        customer = f"cus_test_{uuid.uuid4().hex[:12]}"
        self.customers_created.append(idempotency_key)
        return customer

    def create_setup_intent(self, customer_id: str) -> str:
        return f"seti_{customer_id}_secret"

    def list_cards(self, customer_id: str) -> list[CardSnapshot]:
        return list(self.cards.get(customer_id, []))

    def default_payment_method(self, customer_id: str) -> str | None:
        return None

    def retrieve_payment_method(self, payment_method_id: str) -> PaymentMethodSnapshot | None:
        if payment_method_id not in self.owners:
            return None
        return PaymentMethodSnapshot(
            id=payment_method_id, customer_id=self.owners[payment_method_id]
        )

    def detach_payment_method(self, payment_method_id: str) -> bool:
        if payment_method_id not in self.owners:
            return False
        self.detached.append(payment_method_id)
        del self.owners[payment_method_id]
        return True

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        self.checkout_requests.append(request)
        session_id = f"cs_test_{uuid.uuid4().hex[:12]}"
        return CheckoutSession(id=session_id, client_secret=f"{session_id}_secret")


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    # Explicit: a developer's .env may hold a Stripe key. These tests never call Stripe.
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def stripe(app: Flask, engine: Engine) -> Iterator[StubStripe]:
    """Swaps the Stripe-backed services for the stub, and restores them after the test."""
    stub = StubStripe()
    customers = CustomerDirectory(SqlStripeCustomerStore(engine), stub)
    originals = {
        name: app.extensions[name]
        for name in ("medicalbooking.payment_methods", "medicalbooking.billing")
    }
    app.extensions["medicalbooking.payment_methods"] = PaymentMethodService(
        gateway=stub, customers=customers
    )
    app.extensions["medicalbooking.billing"] = BillingService(
        store=SqlBillingStore(engine),
        gateway=stub,
        clock=lambda: datetime.now(UTC),
        customers=customers,
    )
    yield stub
    app.extensions.update(originals)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _customer_rows(engine: Engine, usuario: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM stripe_clientes WHERE usuario_id = :u"), {"u": usuario}
        ).scalar_one()


def _patient_headers(app: Flask, engine: Engine) -> tuple[str, dict[str, str]]:
    usuario = _user(engine, "PACIENTE")
    return usuario, _token(app, usuario)


def _card(card_id: str) -> CardSnapshot:
    return CardSnapshot(id=card_id, brand="visa", last4="4242", exp_month=3, exp_year=2032)


def _id() -> str:
    return str(uuid.uuid4())


def _seed_user(engine: Engine) -> str:
    usuario = _id()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
                "VALUES (:id, :email, N'Nombre', N'Apellido', SYSUTCDATETIME(), SYSUTCDATETIME())"
            ),
            {"id": usuario, "email": f"{usuario}@example.test"},
        )
    return usuario


def test_migration_creates_the_customer_mapping_with_no_card_columns(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as conn:
        columns = {
            row.COLUMN_NAME
            for row in conn.execute(
                text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME = N'stripe_clientes'"
                )
            )
        }
    # Only the Stripe customer id is stored: no card number, CVC, expiry or PaymentMethod copy.
    assert columns == {"usuario_id", "stripe_customer_id", "creado_en"}


def test_migration_grants_the_payment_method_permission_to_patients_only(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as conn:
        roles = {
            row.nombre
            for row in conn.execute(
                text(
                    "SELECT r.nombre FROM roles_permisos AS rp "
                    "JOIN permisos AS p ON p.id = rp.permiso_id "
                    "JOIN roles AS r ON r.id = rp.rol_id "
                    "WHERE p.codigo = N'metodos_pago.gestionar'"
                )
            )
        }
    assert roles == {"PACIENTE"}


def test_a_user_without_a_customer_is_not_found(migrated_engine: Engine) -> None:
    assert SqlStripeCustomerStore(migrated_engine).find(_seed_user(migrated_engine)) is None


def test_saving_a_second_customer_for_the_same_user_returns_the_stored_one(
    migrated_engine: Engine,
) -> None:
    store = SqlStripeCustomerStore(migrated_engine)
    usuario = _seed_user(migrated_engine)
    assert store.save(usuario, "cus_test_first_0003") == "cus_test_first_0003"
    # A concurrent request that lost the race must reread the row, not fail.
    assert store.save(usuario, "cus_test_second_004") == "cus_test_first_0003"
    assert store.find(usuario) == "cus_test_first_0003"


def test_the_customer_mapping_allows_one_customer_per_user(migrated_engine: Engine) -> None:
    usuario = _seed_user(migrated_engine)
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO stripe_clientes (usuario_id, stripe_customer_id, creado_en) "
                "VALUES (:u, :c, SYSUTCDATETIME())"
            ),
            {"u": usuario, "c": "cus_test_first_0001"},
        )
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO stripe_clientes (usuario_id, stripe_customer_id, creado_en) "
                    "VALUES (:u, :c, SYSUTCDATETIME())"
                ),
                {"u": usuario, "c": "cus_test_second_002"},
            )


# --- routes against SQL Server with the stub ----------------------------------------------

SETUP = "/api/v1/me/payment-methods/setup-intents"
CARDS = "/api/v1/me/payment-methods"


def test_listing_without_a_customer_is_empty_and_creates_no_customer(app, engine, stripe) -> None:
    usuario, headers = _patient_headers(app, engine)
    response = app.test_client().get(CARDS, headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"items": []}
    assert stripe.customers_created == []
    assert _customer_rows(engine, usuario) == 0


def test_setup_intent_creates_one_customer_and_reuses_it(app, engine, stripe) -> None:
    usuario, headers = _patient_headers(app, engine)
    client = app.test_client()
    first = client.post(SETUP, headers=headers)
    second = client.post(SETUP, headers=headers)
    assert first.status_code == second.status_code == 201, first.get_json()
    assert first.get_json()["publishable_key"] == "pk_test_stub_publishable"
    assert stripe.customers_created == [f"customer-{usuario}"]
    assert _customer_rows(engine, usuario) == 1


def test_listed_cards_come_from_stripe_for_the_stored_customer(app, engine, stripe) -> None:
    usuario, headers = _patient_headers(app, engine)
    client = app.test_client()
    client.post(SETUP, headers=headers)
    customer = SqlStripeCustomerStore(engine).find(usuario)
    stripe.cards[customer] = [_card(PM_CARD)]

    response = client.get(CARDS, headers=headers)

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert [item["id"] for item in items] == [PM_CARD]
    assert set(items[0]) == {"id", "brand", "last4", "exp_month", "exp_year", "is_default"}


def test_checkout_opens_the_session_for_the_patients_customer(app, engine, stripe) -> None:
    headers, cita = _booked_appointment(app, engine)
    client = app.test_client()
    factura = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers).get_json()[
        "id"
    ]

    response = client.post(
        "/api/v1/payments/checkout",
        json={"factura_id": factura},
        headers={**headers, "Idempotency-Key": f"checkout-{uuid.uuid4()}"},
    )

    assert response.status_code == 201, response.get_json()
    assert set(response.get_json()) == {"pago_id", "client_secret", "publishable_key"}
    customer = stripe.checkout_requests[0].customer_id
    assert (
        _count(
            engine,
            "SELECT COUNT(*) FROM stripe_clientes WHERE stripe_customer_id = :c",
            c=customer,
        )
        == 1
    )


def test_removing_an_own_card_is_204_and_detaches_it(app, engine, stripe) -> None:
    usuario, headers = _patient_headers(app, engine)
    client = app.test_client()
    client.post(SETUP, headers=headers)
    stripe.owners[PM_CARD] = SqlStripeCustomerStore(engine).find(usuario)

    response = client.delete(f"{CARDS}/{PM_CARD}", headers=headers)

    assert response.status_code == 204
    assert stripe.detached == [PM_CARD]


def test_another_patients_card_and_a_missing_card_answer_the_same_403(app, engine, stripe) -> None:
    owner, owner_headers = _patient_headers(app, engine)
    client = app.test_client()
    client.post(SETUP, headers=owner_headers)
    stripe.owners[PM_CARD] = "cus_test_someone_else"
    _, intruder_headers = _patient_headers(app, engine)

    foreign = client.delete(f"{CARDS}/{PM_CARD}", headers=intruder_headers)
    missing = client.delete(f"{CARDS}/{PM_OTHER}", headers=intruder_headers)

    assert foreign.status_code == missing.status_code == 403
    assert foreign.get_json()["detail"] == missing.get_json()["detail"]
    assert stripe.detached == []


def test_a_doctor_cannot_manage_saved_cards(app, engine, stripe) -> None:
    headers = _token(app, _user(engine, "MEDICO"))
    client = app.test_client()
    assert client.get(CARDS, headers=headers).status_code == 403
    assert client.post(SETUP, headers=headers).status_code == 403
    assert client.delete(f"{CARDS}/{PM_CARD}", headers=headers).status_code == 403
    assert stripe.customers_created == []


def test_setup_intent_without_a_provider_key_answers_503_and_stores_nothing(app, engine) -> None:
    usuario, headers = _patient_headers(app, engine)
    response = app.test_client().post(SETUP, headers=headers)
    assert response.status_code == 503
    assert _customer_rows(engine, usuario) == 0

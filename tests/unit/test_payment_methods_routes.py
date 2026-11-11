"""HTTP contract of /me/payment-methods with the in-memory service: auth, permission, status
codes, exact response shapes and the same answer for missing and foreign cards."""

from __future__ import annotations

import uuid

import pytest
from flask import Flask, jsonify

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.application.payment_methods import PaymentMethodService
from medicalbooking.modules.payments.domain.payment_methods import PERM_PAYMENT_METHODS
from medicalbooking.modules.payments.presentation.routes import (
    PAYMENT_METHODS_EXTENSION,
    payments_bp,
)
from medicalbooking.shared.domain.errors import AppError
from tests.unit.test_payment_methods_customers import FakeCustomerStore
from tests.unit.test_payment_methods_service import CARD, FakeStripe, _card

PUBLISHABLE = "pk_test_fake_publishable"
CARD_KEYS = {"id", "brand", "last4", "exp_month", "exp_year", "is_default"}


class _Tokens:
    def __init__(self, profiles: dict[str, AccessProfile]) -> None:
        self._profiles = profiles

    def authenticate_token(self, token: str) -> AccessProfile:
        if token not in self._profiles:
            raise AppError("bad token")
        return self._profiles[token]


def _profile(permissions=frozenset({PERM_PAYMENT_METHODS}), roles=frozenset({"PACIENTE"})):
    return AccessProfile(
        user_id=str(uuid.uuid4()),
        status="ACTIVO",
        roles=roles,
        permissions=permissions,
    )


def _app(stripe: FakeStripe, store: FakeCustomerStore, profiles: dict[str, AccessProfile]):
    app = Flask("test-payment-methods")
    app.register_blueprint(payments_bp)
    app.extensions[PAYMENT_METHODS_EXTENSION] = PaymentMethodService(
        gateway=stripe, customers=CustomerDirectory(store, stripe)
    )
    app.extensions["medicalbooking.identity"] = _Tokens(profiles)

    @app.errorhandler(AppError)
    def _domain_error(error: AppError):  # noqa: ANN202
        return jsonify({"status": error.status, "title": error.title, "detail": error.detail}), (
            error.status
        )

    return app


@pytest.fixture
def patient():
    profile = _profile()
    return profile, {"tok-patient": profile}


AUTH = {"Authorization": "Bearer tok-patient"}


def test_every_route_requires_a_bearer_token(patient):
    _, profiles = patient
    client = _app(FakeStripe(), FakeCustomerStore(), profiles).test_client()
    assert client.get("/api/v1/me/payment-methods").status_code == 401
    assert client.post("/api/v1/me/payment-methods/setup-intents").status_code == 401
    assert client.delete(f"/api/v1/me/payment-methods/{CARD}").status_code == 401


def test_a_role_without_the_permission_gets_403_on_every_route():
    other = _profile(permissions=frozenset(), roles=frozenset({"MEDICO"}))
    client = _app(FakeStripe(), FakeCustomerStore(), {"tok-other": other}).test_client()
    headers = {"Authorization": "Bearer tok-other"}
    assert client.get("/api/v1/me/payment-methods", headers=headers).status_code == 403
    assert (
        client.post("/api/v1/me/payment-methods/setup-intents", headers=headers).status_code == 403
    )
    assert client.delete(f"/api/v1/me/payment-methods/{CARD}", headers=headers).status_code == 403


def test_listing_without_a_customer_is_200_with_no_items(patient):
    _, profiles = patient
    response = (
        _app(FakeStripe(), FakeCustomerStore(), profiles)
        .test_client()
        .get("/api/v1/me/payment-methods", headers=AUTH)
    )
    assert response.status_code == 200
    assert response.get_json() == {"items": []}


def test_listing_answers_only_the_documented_card_fields(patient):
    profile, profiles = patient
    store = FakeCustomerStore(rows={profile.user_id: "cus_test_owner_0001"})
    stripe = FakeStripe(cards=[_card(CARD, "4242")], default=CARD)
    response = (
        _app(stripe, store, profiles).test_client().get("/api/v1/me/payment-methods", headers=AUTH)
    )

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 1
    assert set(items[0]) == CARD_KEYS
    assert items[0] == {
        "id": CARD,
        "brand": "visa",
        "last4": "4242",
        "exp_month": 12,
        "exp_year": 2031,
        "is_default": True,
    }


def test_setup_intent_answers_201_with_the_secret_and_the_publishable_key(patient):
    _, profiles = patient
    response = (
        _app(FakeStripe(), FakeCustomerStore(), profiles)
        .test_client()
        .post("/api/v1/me/payment-methods/setup-intents", headers=AUTH)
    )
    assert response.status_code == 201
    assert set(response.get_json()) == {"client_secret", "publishable_key"}
    assert response.get_json()["publishable_key"] == PUBLISHABLE


def test_setup_intent_while_unconfigured_is_503_problem_json(patient):
    _, profiles = patient
    response = (
        _app(FakeStripe(configured=False), FakeCustomerStore(), profiles)
        .test_client()
        .post("/api/v1/me/payment-methods/setup-intents", headers=AUTH)
    )
    assert response.status_code == 503


def test_removing_an_own_card_is_204_with_no_body(patient):
    profile, profiles = patient
    store = FakeCustomerStore(rows={profile.user_id: "cus_test_owner_0001"})
    stripe = FakeStripe(owners={CARD: "cus_test_owner_0001"})
    response = (
        _app(stripe, store, profiles)
        .test_client()
        .delete(f"/api/v1/me/payment-methods/{CARD}", headers=AUTH)
    )
    assert response.status_code == 204
    assert response.data == b""
    assert stripe.detached == [CARD]


def test_missing_and_foreign_cards_get_the_same_403(patient):
    profile, profiles = patient
    store = FakeCustomerStore(rows={profile.user_id: "cus_test_owner_0001"})
    stripe = FakeStripe(owners={CARD: "cus_test_someone_else"})
    client = _app(stripe, store, profiles).test_client()

    foreign = client.delete(f"/api/v1/me/payment-methods/{CARD}", headers=AUTH)
    missing = client.delete("/api/v1/me/payment-methods/pm_test_missing_0009", headers=AUTH)

    assert foreign.status_code == missing.status_code == 403
    assert foreign.get_json() == missing.get_json()
    assert stripe.detached == []


def test_a_malformed_card_id_is_403_not_404(patient):
    profile, profiles = patient
    store = FakeCustomerStore(rows={profile.user_id: "cus_test_owner_0001"})
    response = (
        _app(FakeStripe(), store, profiles)
        .test_client()
        .delete("/api/v1/me/payment-methods/not-a-card", headers=AUTH)
    )
    assert response.status_code == 403


def test_a_card_id_with_slashes_is_403_not_404(patient):
    profile, profiles = patient
    store = FakeCustomerStore(rows={profile.user_id: "cus_test_owner_0001"})
    response = (
        _app(FakeStripe(), store, profiles)
        .test_client()
        .delete("/api/v1/me/payment-methods/pm_x/../../customers", headers=AUTH)
    )
    assert response.status_code == 403

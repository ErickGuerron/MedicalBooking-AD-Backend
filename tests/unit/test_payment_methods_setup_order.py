"""S6: a failed SetupIntent must not leave a customer mapping stored locally.

Uses the real Stripe gateway over httpx.MockTransport, so the request order is the one Stripe sees.
"""

from __future__ import annotations

import httpx
import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.application.payment_methods import PaymentMethodService
from medicalbooking.modules.payments.infrastructure.stripe_checkout import StripeCheckoutGateway
from medicalbooking.shared.domain.errors import ServiceUnavailable

ACTOR = AccessProfile(
    user_id="u9",
    status="active",
    roles=frozenset({"PACIENTE"}),
    permissions=frozenset({"metodos_pago.gestionar"}),
)


class _Store:
    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}

    def find(self, usuario_id: str) -> str | None:
        return self.mapping.get(usuario_id)

    def save(self, usuario_id: str, stripe_customer_id: str) -> str:
        return self.mapping.setdefault(usuario_id, stripe_customer_id)


def _service(handler, store: _Store) -> PaymentMethodService:
    client = httpx.Client(
        base_url="https://api.stripe.test", transport=httpx.MockTransport(handler)
    )
    gateway = StripeCheckoutGateway(
        secret_key="sk_test_synthetic",
        return_url="https://front.test/pago/retorno",
        publishable_key="pk_test_synthetic",
        client=client,
    )
    return PaymentMethodService(gateway, CustomerDirectory(store, gateway))


def test_a_failed_setup_intent_stores_no_customer_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_new"})
        return httpx.Response(503, json={"error": "down"})

    store = _Store()
    with pytest.raises(ServiceUnavailable):
        _service(handler, store).start_setup(ACTOR)
    assert store.find("u9") is None


def test_a_successful_setup_intent_stores_the_customer_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_new"})
        return httpx.Response(200, json={"client_secret": "seti_1_secret_x"})

    store = _Store()
    start = _service(handler, store).start_setup(ACTOR)
    assert start.client_secret == "seti_1_secret_x"
    assert store.find("u9") == "cus_new"

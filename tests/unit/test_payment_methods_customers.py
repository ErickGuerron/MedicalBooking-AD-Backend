"""The per-user Stripe customer: created lazily with a per-user idempotency key, reused after."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.shared.domain.errors import ServiceUnavailable


@dataclass
class FakeCustomerStore:
    rows: dict[str, str] = field(default_factory=dict)
    saves: list[tuple[str, str]] = field(default_factory=list)

    def find(self, usuario_id: str) -> str | None:
        return self.rows.get(usuario_id)

    def save(self, usuario_id: str, stripe_customer_id: str) -> str:
        self.saves.append((usuario_id, stripe_customer_id))
        # Like the SQL adapter: the first stored mapping wins over a concurrent one.
        return self.rows.setdefault(usuario_id, stripe_customer_id)


@dataclass
class FakeCustomerGateway:
    configured: bool = True
    fail: bool = False
    customer_id: str = "cus_test_created_0001"
    keys: list[str] = field(default_factory=list)

    def is_configured(self) -> bool:
        return self.configured

    def create_customer(self, idempotency_key: str) -> str:
        self.keys.append(idempotency_key)
        if self.fail:
            raise ProviderUnavailable("provider down")
        return self.customer_id


def test_the_first_request_creates_the_customer_with_the_per_user_idempotency_key():
    usuario = str(uuid.uuid4())
    store, gateway = FakeCustomerStore(), FakeCustomerGateway()

    customer = CustomerDirectory(store, gateway).ensure(usuario)

    assert customer == "cus_test_created_0001"
    assert gateway.keys == [f"customer-{usuario}"]
    assert store.saves == [(usuario, "cus_test_created_0001")]


def test_an_existing_customer_is_reused_and_the_provider_is_not_called():
    usuario = str(uuid.uuid4())
    store = FakeCustomerStore(rows={usuario: "cus_test_existing_01"})
    gateway = FakeCustomerGateway()

    assert CustomerDirectory(store, gateway).ensure(usuario) == "cus_test_existing_01"
    assert gateway.keys == []
    assert store.saves == []


def test_find_never_calls_the_provider():
    usuario = str(uuid.uuid4())
    gateway = FakeCustomerGateway()
    directory = CustomerDirectory(FakeCustomerStore(), gateway)

    assert directory.find(usuario) is None
    assert gateway.keys == []


def test_creation_is_refused_locally_when_the_provider_is_not_configured():
    usuario = str(uuid.uuid4())
    store, gateway = FakeCustomerStore(), FakeCustomerGateway(configured=False)

    with pytest.raises(ServiceUnavailable):
        CustomerDirectory(store, gateway).ensure(usuario)
    assert gateway.keys == []
    assert store.saves == []


def test_provider_failure_while_creating_stores_nothing():
    usuario = str(uuid.uuid4())
    store, gateway = FakeCustomerStore(), FakeCustomerGateway(fail=True)

    with pytest.raises(ServiceUnavailable):
        CustomerDirectory(store, gateway).ensure(usuario)
    assert store.saves == []


class _RacingStore(FakeCustomerStore):
    """The first read misses, as it does for a request that lost the race to store a row."""

    def find(self, usuario_id: str) -> str | None:
        return None


def test_a_concurrent_request_that_stored_first_wins_over_its_own_customer():
    usuario = str(uuid.uuid4())
    store = _RacingStore(rows={usuario: "cus_test_winner_0001"})
    gateway = FakeCustomerGateway(customer_id="cus_test_loser_00002")

    customer = CustomerDirectory(store, gateway).ensure(usuario)

    assert customer == "cus_test_winner_0001"

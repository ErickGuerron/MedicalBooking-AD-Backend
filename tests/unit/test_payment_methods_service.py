"""Saved payment methods use cases against in-memory fakes: permission, ownership, provider
failures and the rule that no card data is stored locally."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.application.payment_methods import (
    CardSnapshot,
    PaymentMethodService,
    PaymentMethodSnapshot,
)
from medicalbooking.modules.payments.domain.payment_methods import (
    PERM_PAYMENT_METHODS,
    is_payment_method_id,
)
from medicalbooking.shared.domain.errors import Forbidden, ServiceUnavailable
from tests.unit.test_payment_methods_customers import FakeCustomerStore

PUBLISHABLE = "pk_test_fake_publishable"
CARD = "pm_test_card_0001"
OTHER_CARD = "pm_test_card_0002"


def _id() -> str:
    return str(uuid.uuid4())


def _patient(user_id: str | None = None, permissions=None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset({"PACIENTE"}),
        permissions=frozenset({PERM_PAYMENT_METHODS}) if permissions is None else permissions,
    )


@dataclass
class FakeStripe:
    """Stands in for the Stripe adapter. Records every call; nothing reaches the network."""

    configured: bool = True
    fail: bool = False
    customer_id: str = "cus_test_created_0001"
    cards: list[CardSnapshot] = field(default_factory=list)
    default: str | None = None
    owners: dict[str, str | None] = field(default_factory=dict)
    detach_result: bool = True
    calls: list[str] = field(default_factory=list)
    detached: list[str] = field(default_factory=list)
    created_customers: list[str] = field(default_factory=list)
    setup_customers: list[str] = field(default_factory=list)
    publishable_key: str | None = PUBLISHABLE

    def is_configured(self) -> bool:
        return self.configured

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail:
            raise ProviderUnavailable("provider down with a secret inside")

    def create_customer(self, idempotency_key: str) -> str:
        self._maybe_fail("create_customer")
        self.created_customers.append(idempotency_key)
        return self.customer_id

    def create_setup_intent(self, customer_id: str) -> str:
        self._maybe_fail("create_setup_intent")
        self.setup_customers.append(customer_id)
        return "seti_test_secret_0001"

    def list_cards(self, customer_id: str) -> list[CardSnapshot]:
        self._maybe_fail("list_cards")
        return list(self.cards)

    def default_payment_method(self, customer_id: str) -> str | None:
        self._maybe_fail("default_payment_method")
        return self.default

    def retrieve_payment_method(self, payment_method_id: str) -> PaymentMethodSnapshot | None:
        self._maybe_fail("retrieve_payment_method")
        if payment_method_id not in self.owners:
            return None
        return PaymentMethodSnapshot(
            id=payment_method_id, customer_id=self.owners[payment_method_id]
        )

    def detach_payment_method(self, payment_method_id: str) -> bool:
        self._maybe_fail("detach_payment_method")
        self.detached.append(payment_method_id)
        return self.detach_result


@dataclass
class _World:
    service: PaymentMethodService
    stripe: FakeStripe
    store: FakeCustomerStore


def _world(stripe: FakeStripe | None = None, store: FakeCustomerStore | None = None) -> _World:
    stripe = stripe or FakeStripe()
    store = store or FakeCustomerStore()
    customers = CustomerDirectory(store, stripe)
    return _World(PaymentMethodService(gateway=stripe, customers=customers), stripe, store)


def _card(card_id: str = CARD, last4: str = "4242") -> CardSnapshot:
    return CardSnapshot(id=card_id, brand="visa", last4=last4, exp_month=12, exp_year=2031)


# --- permission ----------------------------------------------------------------


def test_listing_requires_the_permission():
    world = _world()
    with pytest.raises(Forbidden):
        world.service.list_cards(_patient(permissions=frozenset()))


def test_setup_requires_the_permission():
    world = _world()
    with pytest.raises(Forbidden):
        world.service.start_setup(_patient(permissions=frozenset()))
    assert world.stripe.calls == []


def test_removal_requires_the_permission():
    owner = _id()
    world = _world(store=FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(Forbidden):
        world.service.remove_card(_patient(owner, permissions=frozenset()), payment_method_id=CARD)
    assert world.stripe.calls == []


# --- listing -------------------------------------------------------------------


def test_listing_without_a_customer_is_empty_and_never_touches_stripe():
    world = _world()
    assert world.service.list_cards(_patient()) == []
    assert world.stripe.calls == []
    assert world.store.saves == []


def test_listing_maps_each_card_and_marks_the_default():
    owner = _id()
    stripe = FakeStripe(
        cards=[_card(CARD, "4242"), _card(OTHER_CARD, "0005")],
        default=OTHER_CARD,
    )
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))

    cards = world.service.list_cards(_patient(owner))

    assert [(c.id, c.brand, c.last4, c.is_default) for c in cards] == [
        (CARD, "visa", "4242", False),
        (OTHER_CARD, "visa", "0005", True),
    ]
    assert (cards[0].exp_month, cards[0].exp_year) == (12, 2031)


def test_listing_fails_closed_when_the_provider_is_unavailable():
    owner = _id()
    stripe = FakeStripe(fail=True)
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(ServiceUnavailable) as error:
        world.service.list_cards(_patient(owner))
    assert "secret" not in error.value.detail


# --- setup intent --------------------------------------------------------------


def test_setup_creates_the_customer_then_the_setup_intent_and_returns_the_key():
    world = _world()
    usuario = _id()

    start = world.service.start_setup(_patient(usuario))

    assert start.client_secret == "seti_test_secret_0001"
    assert start.publishable_key == PUBLISHABLE
    assert world.stripe.created_customers == [f"customer-{usuario}"]
    assert world.stripe.setup_customers == ["cus_test_created_0001"]
    assert world.store.saves == [(usuario, "cus_test_created_0001")]


def test_setup_is_refused_before_anything_is_stored_when_unconfigured():
    world = _world(FakeStripe(configured=False))
    with pytest.raises(ServiceUnavailable):
        world.service.start_setup(_patient())
    assert world.store.saves == []
    assert world.stripe.calls == []


def test_setup_provider_failure_is_unavailable_without_provider_detail():
    world = _world(FakeStripe(fail=True))
    with pytest.raises(ServiceUnavailable) as error:
        world.service.start_setup(_patient())
    assert "secret" not in error.value.detail
    assert world.store.saves == []


# --- removal -------------------------------------------------------------------


def test_removing_an_own_card_detaches_it():
    owner = _id()
    stripe = FakeStripe(owners={CARD: "cus_test_owner_0001"})
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))

    world.service.remove_card(_patient(owner), payment_method_id=CARD)

    assert stripe.detached == [CARD]


def test_removing_a_foreign_card_is_forbidden_and_detaches_nothing():
    owner = _id()
    stripe = FakeStripe(owners={CARD: "cus_test_someone_else"})
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))

    with pytest.raises(Forbidden):
        world.service.remove_card(_patient(owner), payment_method_id=CARD)
    assert stripe.detached == []


def test_a_missing_card_answers_the_same_as_a_foreign_one():
    owner = _id()
    stripe = FakeStripe(owners={CARD: "cus_test_someone_else"})
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(Forbidden) as foreign:
        world.service.remove_card(_patient(owner), payment_method_id=CARD)
    with pytest.raises(Forbidden) as missing:
        world.service.remove_card(_patient(owner), payment_method_id="pm_test_missing_0009")
    assert foreign.value.detail == missing.value.detail


def test_removing_without_a_customer_is_forbidden_without_calling_stripe():
    world = _world(FakeStripe(owners={CARD: "cus_test_someone_else"}))
    with pytest.raises(Forbidden):
        world.service.remove_card(_patient(), payment_method_id=CARD)
    assert world.stripe.calls == []


@pytest.mark.parametrize("bad_id", ["cus_test_0001", "pm_", "pm_x/../../v1", "pm_" + "a" * 300])
def test_a_malformed_card_id_is_forbidden_without_calling_stripe(bad_id):
    owner = _id()
    world = _world(FakeStripe(), FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(Forbidden):
        world.service.remove_card(_patient(owner), payment_method_id=bad_id)
    assert world.stripe.calls == []


def test_a_card_that_disappears_before_detach_is_forbidden():
    owner = _id()
    stripe = FakeStripe(owners={CARD: "cus_test_owner_0001"}, detach_result=False)
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(Forbidden):
        world.service.remove_card(_patient(owner), payment_method_id=CARD)


def test_removal_provider_failure_is_unavailable():
    owner = _id()
    stripe = FakeStripe(fail=True)
    world = _world(stripe, FakeCustomerStore(rows={owner: "cus_test_owner_0001"}))
    with pytest.raises(ServiceUnavailable):
        world.service.remove_card(_patient(owner), payment_method_id=CARD)


# --- domain --------------------------------------------------------------------


@pytest.mark.parametrize("valid", ["pm_1Q0PsIJvEtkwdCNYMSaVuRz6", "pm_test_card_0001"])
def test_stripe_payment_method_ids_are_accepted(valid):
    assert is_payment_method_id(valid) is True


@pytest.mark.parametrize(
    "invalid",
    ["", "pm_", "cus_test_0001", "pm_x/../v1", "pm_x?y=1", "PM_test", "pm_" + "a" * 300],
)
def test_anything_else_is_not_a_payment_method_id(invalid):
    assert is_payment_method_id(invalid) is False

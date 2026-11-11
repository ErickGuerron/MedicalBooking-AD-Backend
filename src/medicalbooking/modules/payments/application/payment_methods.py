"""Saved payment methods: use cases and the snapshots the provider adapter returns.

Only the Stripe customer id is stored locally. Cards are read from Stripe on every listing and
never copied into the database. Missing and foreign cards share one Forbidden answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.domain.payment_methods import (
    PERM_PAYMENT_METHODS,
    is_payment_method_id,
)
from medicalbooking.shared.domain.errors import Forbidden, ServiceUnavailable


@dataclass(frozen=True)
class CardSnapshot:
    """A saved card as Stripe describes it. Display data only, never the card number."""

    id: str
    brand: str
    last4: str
    exp_month: int | None
    exp_year: int | None


@dataclass(frozen=True)
class PaymentMethodSnapshot:
    """The owner of a PaymentMethod. customer_id is None once the card is detached."""

    id: str
    customer_id: str | None


@dataclass(frozen=True)
class SavedCard:
    id: str
    brand: str
    last4: str
    exp_month: int | None
    exp_year: int | None
    is_default: bool


@dataclass(frozen=True)
class SetupStart:
    client_secret: str
    publishable_key: str


class PaymentMethodsGateway(Protocol):
    publishable_key: str | None

    def is_configured(self) -> bool: ...

    def create_setup_intent(self, customer_id: str) -> str: ...

    def list_cards(self, customer_id: str) -> list[CardSnapshot]: ...

    def default_payment_method(self, customer_id: str) -> str | None: ...

    def retrieve_payment_method(self, payment_method_id: str) -> PaymentMethodSnapshot | None: ...

    def detach_payment_method(self, payment_method_id: str) -> bool: ...


class PaymentMethodService:
    def __init__(self, gateway: PaymentMethodsGateway, customers: CustomerDirectory) -> None:
        self._gateway = gateway
        self._customers = customers

    def list_cards(self, actor: AccessProfile) -> list[SavedCard]:
        _require(actor)
        customer_id = self._customers.find(actor.user_id)
        if customer_id is None:
            # No customer means no saved cards. Stripe is not asked and no customer is created.
            return []
        self._ensure_configured()
        cards = self._provider(lambda: self._gateway.list_cards(customer_id))
        default = self._provider(lambda: self._gateway.default_payment_method(customer_id))
        return [
            SavedCard(
                id=card.id,
                brand=card.brand,
                last4=card.last4,
                exp_month=card.exp_month,
                exp_year=card.exp_year,
                is_default=card.id == default,
            )
            for card in cards
        ]

    def start_setup(self, actor: AccessProfile) -> SetupStart:
        _require(actor)
        # Refused before any local write: an unconfigured provider stores no customer.
        self._ensure_configured()
        client_secret = self._customers.with_customer(
            actor.user_id,
            lambda customer_id: self._provider(
                lambda: self._gateway.create_setup_intent(customer_id)
            ),
        )
        return SetupStart(client_secret=client_secret, publishable_key=self._publishable_key())

    def remove_card(self, actor: AccessProfile, *, payment_method_id: str) -> None:
        _require(actor)
        # A malformed id is treated as a missing card: Stripe is never asked about it.
        if not is_payment_method_id(payment_method_id):
            raise _forbidden()
        customer_id = self._customers.find(actor.user_id)
        if customer_id is None:
            raise _forbidden()
        self._ensure_configured()
        card = self._provider(lambda: self._gateway.retrieve_payment_method(payment_method_id))
        if card is None or card.customer_id != customer_id:
            raise _forbidden()
        detached = self._provider(lambda: self._gateway.detach_payment_method(payment_method_id))
        if not detached:
            raise _forbidden()

    def _ensure_configured(self) -> None:
        if not self._gateway.is_configured():
            raise ServiceUnavailable("Payment provider is not configured")

    def _publishable_key(self) -> str:
        key = self._gateway.publishable_key
        if not key:
            raise ServiceUnavailable("Payment provider is not configured")
        return key

    def _provider[T](self, call: Callable[[], T]) -> T:
        try:
            return call()
        except ProviderUnavailable as error:
            raise ServiceUnavailable("Payment provider is unavailable") from error


def _require(actor: AccessProfile) -> None:
    if PERM_PAYMENT_METHODS not in actor.permissions:
        raise _forbidden()


def _forbidden() -> Forbidden:
    return Forbidden("Access to this resource is not allowed")

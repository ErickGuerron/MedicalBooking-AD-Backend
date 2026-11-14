"""The Stripe customer of each user (S3). One customer per user, created on first need.

Checkout and saved payment methods both go through here, so a user never gets two customers.
The provider call uses the Idempotency-Key customer-<usuario_id>: two concurrent first requests
receive the same customer, and the store keeps the first mapping that was written.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.shared.domain.errors import ServiceUnavailable

T = TypeVar("T")


class CustomerStore(Protocol):
    def find(self, usuario_id: str) -> str | None: ...

    def save(self, usuario_id: str, stripe_customer_id: str) -> str:
        """Store the mapping and return the mapping that is stored afterwards."""
        ...


class CustomerGateway(Protocol):
    def is_configured(self) -> bool: ...

    def create_customer(self, idempotency_key: str) -> str: ...


class CustomerDirectory:
    def __init__(self, store: CustomerStore, gateway: CustomerGateway) -> None:
        self._store = store
        self._gateway = gateway

    def find(self, usuario_id: str) -> str | None:
        """The user's customer if one exists. Never creates one and never calls the provider."""
        return self._store.find(usuario_id)

    def ensure(self, usuario_id: str) -> str:
        existing = self._store.find(usuario_id)
        if existing is not None:
            return existing
        customer_id = self._create(usuario_id)
        return self._store.save(usuario_id, customer_id)

    def with_customer(self, usuario_id: str, action: Callable[[str], T]) -> T:
        """Run a provider action for the user's customer and store the mapping only after it
        succeeds, so a failed action leaves no local customer behind. Retries are safe because
        the provider returns the same customer for the same idempotency key."""
        existing = self._store.find(usuario_id)
        if existing is not None:
            return action(existing)
        customer_id = self._create(usuario_id)
        result = action(customer_id)
        self._store.save(usuario_id, customer_id)
        return result

    def _create(self, usuario_id: str) -> str:
        if not self._gateway.is_configured():
            raise ServiceUnavailable("Payment provider is not configured")
        try:
            return self._gateway.create_customer(f"customer-{usuario_id}")
        except ProviderUnavailable as error:
            raise ServiceUnavailable("Payment provider is unavailable") from error

"""Refund status: the owner reads the newest refund of a payment;
a pending one is checked at Stripe first."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.refund_service import LatestRefund, RefundService
from medicalbooking.modules.payments.domain.billing import PERM_CHECKOUT
from medicalbooking.shared.domain.errors import Forbidden

OWNER = "owner-1"
OTHER = "other-1"
PAGO = "11111111-1111-1111-1111-111111111111"
REEMBOLSO = "22222222-2222-2222-2222-222222222222"


def _actor(user_id: str) -> AccessProfile:
    return AccessProfile(
        user_id=user_id,
        status="ACTIVO",
        roles=frozenset({"PACIENTE"}),
        permissions=frozenset({PERM_CHECKOUT}),
    )


@dataclass
class Store:
    latest: LatestRefund | None

    def find_latest_refund(self, pago_id):
        return self.latest


@dataclass
class Checker:
    """Stands for the Stripe check: it settles the refund in the store when told to."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    settle_to: str | None = None
    store: Store | None = None

    def __call__(self, reembolso_id, stripe_refund_id):
        self.calls.append((reembolso_id, stripe_refund_id))
        if self.settle_to and self.store and self.store.latest:
            self.store.latest = LatestRefund(
                id=reembolso_id,
                estado=self.settle_to,
                stripe_refund_id=stripe_refund_id,
                owner_user_id=self.store.latest.owner_user_id,
            )
        return "PROCESADO"


def _service(store: Store, checker=None) -> RefundService:
    return RefundService(store=store, gateway=None, clock=lambda: None, checker=checker)


def test_no_refund_reports_none() -> None:
    assert _service(Store(latest=None)).status(_actor(OWNER), pago_id=PAGO) is None


def test_pending_refund_is_checked_at_stripe_and_reports_the_final_state() -> None:
    store = Store(latest=LatestRefund(REEMBOLSO, "PENDIENTE", "re_123", OWNER))
    checker = Checker(settle_to="PROCESADO", store=store)
    state = _service(store, checker).status(_actor(OWNER), pago_id=PAGO)
    assert checker.calls == [(REEMBOLSO, "re_123")]
    assert state == "PROCESADO"


def test_pending_refund_without_stripe_id_is_not_checked() -> None:
    store = Store(latest=LatestRefund(REEMBOLSO, "PENDIENTE", None, OWNER))
    checker = Checker()
    assert _service(store, checker).status(_actor(OWNER), pago_id=PAGO) == "PENDIENTE"
    assert checker.calls == []


def test_a_settled_refund_is_not_checked_again() -> None:
    store = Store(latest=LatestRefund(REEMBOLSO, "PROCESADO", "re_123", OWNER))
    checker = Checker()
    assert _service(store, checker).status(_actor(OWNER), pago_id=PAGO) == "PROCESADO"
    assert checker.calls == []


def test_another_patient_cannot_read_the_refund() -> None:
    store = Store(latest=LatestRefund(REEMBOLSO, "PENDIENTE", "re_123", OWNER))
    with pytest.raises(Forbidden):
        _service(store, Checker()).status(_actor(OTHER), pago_id=PAGO)

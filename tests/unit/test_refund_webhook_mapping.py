"""Stripe refund.updated mapping: refund status to reembolso outcome (pure, no database)."""

from __future__ import annotations

import pytest

from medicalbooking.modules.payments.domain.webhook import (
    FALLIDO,
    PROCESADO,
    RefundChange,
    interpret_refund_event,
    refund_target_status,
)


@pytest.mark.parametrize(
    ("status", "target"),
    [
        ("succeeded", PROCESADO),
        ("failed", FALLIDO),
        ("canceled", FALLIDO),
        ("pending", None),
        ("requires_action", None),
        ("unknown_future_status", None),
        ("", None),
        (None, None),
    ],
)
def test_refund_status_maps_to_reembolso_outcome(status: str | None, target: str | None) -> None:
    assert refund_target_status(status) == target


def test_refund_updated_event_yields_refund_id_and_target() -> None:
    event = {
        "id": "evt_refund_1",
        "type": "refund.updated",
        "data": {"object": {"id": "re_test_1", "status": "succeeded", "amount": 5000}},
    }

    assert interpret_refund_event(event) == RefundChange(
        refund_id="re_test_1", target_status=PROCESADO
    )


def test_refund_event_without_object_has_no_refund_id_and_no_change() -> None:
    event = {"id": "evt_refund_2", "type": "refund.updated", "data": {}}

    assert interpret_refund_event(event) == RefundChange(refund_id=None, target_status=None)


def test_pending_refund_event_is_a_no_change() -> None:
    event = {
        "id": "evt_refund_3",
        "type": "refund.updated",
        "data": {"object": {"id": "re_test_3", "status": "pending"}},
    }

    assert interpret_refund_event(event).target_status is None

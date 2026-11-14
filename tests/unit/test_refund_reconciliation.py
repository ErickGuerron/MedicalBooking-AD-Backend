"""Refund reconciliation: stale selection, provider status mapping, resend, failures, the Stripe
refund calls it needs, and the worker's --help and configuration exit. No network: Stripe is an
httpx.MockTransport or an in-memory fake, and no database is opened."""

from __future__ import annotations

import logging
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.application.refund_reconciliation import (
    STALE_AFTER,
    RefundReconciliationService,
    StaleRefund,
)
from medicalbooking.modules.payments.application.refund_service import ProviderRejected
from medicalbooking.modules.payments.domain.webhook import RefundChange
from medicalbooking.modules.payments.infrastructure.stripe_checkout import (
    REFUNDS_PATH,
    STRIPE_API_BASE,
    StripeCheckoutGateway,
)
from medicalbooking.workers import reconcile_refunds

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
REEMBOLSO = "11111111-1111-1111-1111-111111111111"
RE_STRIPE = "re_test_reconcile_0001"
PAYMENT_INTENT = "pi_test_reconcile_0001"
SECRET_IN_ERROR = "sk_live_must_never_be_logged"


@dataclass(frozen=True)
class RemoteRefund:
    id: str
    status: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeRefundStore:
    failed: list[str] = field(default_factory=list)
    answers: list[tuple[str, str, str]] = field(default_factory=list)

    def mark_failed(self, refund_id: str) -> None:
        self.failed.append(refund_id)

    def record_provider_answer(self, refund_id: str, stripe_refund_id: str, estado: str) -> None:
        self.answers.append((refund_id, stripe_refund_id, estado))


@dataclass
class FakePayments:
    outcome: str = "PROCESADO"
    calls: list[tuple[str, str, RefundChange]] = field(default_factory=list)

    def process_refund(self, event_id: str, event_type: str, change: RefundChange) -> str:
        self.calls.append((event_id, event_type, change))
        return self.outcome


class FakeGateway:
    def __init__(self) -> None:
        self.status = "pending"
        self.get_error: Exception | None = None
        self.listed: list[RemoteRefund] = []
        self.list_error: Exception | None = None
        self.create_result: RemoteRefund | None = None
        self.create_error: Exception | None = None
        self.get_calls: list[str] = []
        self.list_calls: list[str] = []
        self.create_calls: list[tuple[str, int, str, dict[str, str]]] = []

    def get_refund(self, stripe_refund_id: str) -> RemoteRefund:
        self.get_calls.append(stripe_refund_id)
        if self.get_error is not None:
            raise self.get_error
        return RemoteRefund(id=stripe_refund_id, status=self.status)

    def list_refunds_for_payment_intent(self, payment_intent_id: str) -> list[RemoteRefund]:
        self.list_calls.append(payment_intent_id)
        if self.list_error is not None:
            raise self.list_error
        return list(self.listed)

    def create_refund(
        self,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
        metadata: dict[str, str],
    ) -> RemoteRefund:
        self.create_calls.append((payment_intent_id, amount_cents, idempotency_key, dict(metadata)))
        if self.create_error is not None:
            raise self.create_error
        return self.create_result or RemoteRefund(
            id=RE_STRIPE, status="pending", metadata=dict(metadata)
        )


def _stale(stripe_refund_id: str | None = RE_STRIPE, payment_intent: str | None = PAYMENT_INTENT):
    return StaleRefund(
        id=REEMBOLSO,
        stripe_refund_id=stripe_refund_id,
        payment_intent_id=payment_intent,
        monto=Decimal("25.00"),
        solicitado_en=NOW - timedelta(minutes=20),
    )


def _service(rows, *, refunds=None, payments=None, gateway=None):
    seen: dict[str, datetime] = {}

    def find_stale(cutoff: datetime) -> list[StaleRefund]:
        seen["cutoff"] = cutoff
        return list(rows)

    service = RefundReconciliationService(
        find_stale=find_stale,
        refunds=refunds or FakeRefundStore(),
        payments=payments or FakePayments(),
        gateway=gateway or FakeGateway(),
        clock=lambda: NOW,
    )
    return service, seen


# ---------- stale selection ----------


def test_stale_means_created_more_than_fifteen_minutes_ago() -> None:
    assert STALE_AFTER == timedelta(minutes=15)
    service, seen = _service([])

    service.run()

    assert seen["cutoff"] == NOW - timedelta(minutes=15)


def test_no_stale_refund_means_no_provider_call() -> None:
    gateway = FakeGateway()
    service, _ = _service([], gateway=gateway)

    report = service.run()

    assert report.examined == 0
    assert gateway.get_calls == [] and gateway.list_calls == [] and gateway.create_calls == []


# ---------- provider status mapping (refund already has its Stripe id) ----------


@pytest.mark.parametrize(
    ("provider_status", "target"),
    [("succeeded", "PROCESADO"), ("failed", "FALLIDO"), ("canceled", "FALLIDO")],
)
def test_a_terminal_provider_status_is_applied_through_the_webhook_path(
    provider_status: str, target: str
) -> None:
    gateway = FakeGateway()
    gateway.status = provider_status
    payments = FakePayments()
    service, _ = _service([_stale()], payments=payments, gateway=gateway)

    report = service.run()

    assert gateway.get_calls == [RE_STRIPE]
    assert gateway.create_calls == []
    assert payments.calls == [
        (
            f"reconcile-{RE_STRIPE}-{target}",
            "refund.reconciled",
            RefundChange(refund_id=RE_STRIPE, target_status=target),
        )
    ]
    assert report.finalized == 1


@pytest.mark.parametrize("provider_status", ["pending", "requires_action", "unknown"])
def test_a_non_terminal_provider_status_leaves_the_refund_pending(provider_status: str) -> None:
    gateway = FakeGateway()
    gateway.status = provider_status
    payments = FakePayments()
    service, _ = _service([_stale()], payments=payments, gateway=gateway)

    report = service.run()

    assert payments.calls == []
    assert report.kept_pending == 1


def test_a_transient_provider_failure_on_read_leaves_everything_untouched() -> None:
    gateway = FakeGateway()
    gateway.get_error = ProviderUnavailable("Stripe is unreachable")
    payments = FakePayments()
    refunds = FakeRefundStore()
    service, _ = _service([_stale()], refunds=refunds, payments=payments, gateway=gateway)

    report = service.run()

    assert payments.calls == [] and refunds.answers == [] and refunds.failed == []
    assert report.kept_pending == 1


# ---------- refund without a Stripe id: find it by metadata, or resend ----------


def test_a_refund_the_provider_already_holds_is_found_by_metadata_and_recorded() -> None:
    gateway = FakeGateway()
    gateway.status = "succeeded"
    gateway.listed = [RemoteRefund(RE_STRIPE, "succeeded", {"reembolso_id": REEMBOLSO})]
    refunds = FakeRefundStore()
    payments = FakePayments()
    service, _ = _service(
        [_stale(stripe_refund_id=None)], refunds=refunds, payments=payments, gateway=gateway
    )

    service.run()

    assert gateway.list_calls == [PAYMENT_INTENT]
    assert gateway.create_calls == []
    assert refunds.answers == [(REEMBOLSO, RE_STRIPE, "PENDIENTE")]
    assert payments.calls[0][2] == RefundChange(refund_id=RE_STRIPE, target_status="PROCESADO")


def test_listed_refunds_with_another_reembolso_id_are_not_matched() -> None:
    gateway = FakeGateway()
    gateway.listed = [RemoteRefund("re_other", "succeeded", {"reembolso_id": "other-id"})]
    service, _ = _service([_stale(stripe_refund_id=None)], gateway=gateway)

    service.run()

    assert len(gateway.create_calls) == 1


def test_a_refund_the_provider_never_saw_is_resent_with_the_same_key_and_metadata() -> None:
    gateway = FakeGateway()
    refunds = FakeRefundStore()
    service, _ = _service([_stale(stripe_refund_id=None)], refunds=refunds, gateway=gateway)

    report = service.run()

    assert gateway.create_calls == [
        (PAYMENT_INTENT, 2500, f"refund-{REEMBOLSO}", {"reembolso_id": REEMBOLSO})
    ]
    assert refunds.answers == [(REEMBOLSO, RE_STRIPE, "PENDIENTE")]
    assert report.kept_pending == 1


def test_a_definite_refusal_on_resend_marks_the_refund_failed() -> None:
    gateway = FakeGateway()
    gateway.create_error = ProviderRejected("Stripe refused the request (400)")
    refunds = FakeRefundStore()
    payments = FakePayments()
    service, _ = _service(
        [_stale(stripe_refund_id=None)], refunds=refunds, payments=payments, gateway=gateway
    )

    report = service.run()

    assert refunds.failed == [REEMBOLSO]
    assert refunds.answers == []
    assert payments.calls == []
    assert report.failed == 1


def test_a_transient_failure_on_resend_leaves_the_refund_pending() -> None:
    gateway = FakeGateway()
    gateway.create_error = ProviderUnavailable("Stripe answered 500")
    refunds = FakeRefundStore()
    service, _ = _service([_stale(stripe_refund_id=None)], refunds=refunds, gateway=gateway)

    report = service.run()

    assert refunds.failed == [] and refunds.answers == []
    assert report.kept_pending == 1


def test_a_transient_failure_on_the_metadata_lookup_leaves_the_refund_pending() -> None:
    gateway = FakeGateway()
    gateway.list_error = ProviderUnavailable("Stripe is unreachable")
    refunds = FakeRefundStore()
    service, _ = _service([_stale(stripe_refund_id=None)], refunds=refunds, gateway=gateway)

    report = service.run()

    assert gateway.create_calls == [], "an unreadable lookup must not create a second refund"
    assert refunds.failed == [] and refunds.answers == []
    assert report.kept_pending == 1


def test_a_stale_refund_without_payment_intent_fails_without_any_provider_call() -> None:
    gateway = FakeGateway()
    refunds = FakeRefundStore()
    service, _ = _service(
        [_stale(stripe_refund_id=None, payment_intent=None)], refunds=refunds, gateway=gateway
    )

    service.run()

    assert refunds.failed == [REEMBOLSO]
    assert gateway.list_calls == [] and gateway.create_calls == []


# ---------- report and logging ----------


def test_a_repeated_application_is_reported_as_already_applied() -> None:
    gateway = FakeGateway()
    gateway.status = "succeeded"
    payments = FakePayments(outcome="DUPLICADO")
    service, _ = _service([_stale()], payments=payments, gateway=gateway)

    report = service.run()

    assert report.finalized == 0
    assert report.already_applied == 1


def test_logs_carry_counts_and_error_types_never_ids_or_provider_text(caplog) -> None:
    gateway = FakeGateway()
    gateway.create_error = ProviderUnavailable(f"Stripe answered 500 {SECRET_IN_ERROR}")
    service, _ = _service([_stale(stripe_refund_id=None)], gateway=gateway)

    with caplog.at_level(logging.DEBUG):
        service.run()

    text = caplog.text
    assert REEMBOLSO not in text and RE_STRIPE not in text
    assert SECRET_IN_ERROR not in text and PAYMENT_INTENT not in text


# ---------- Stripe adapter calls used by the reconciler ----------


def _stripe(handler) -> StripeCheckoutGateway:
    return StripeCheckoutGateway(
        secret_key="sk_test_synthetic_reconcile",
        return_url="",
        client=httpx.Client(base_url=STRIPE_API_BASE, transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
    )


def test_create_refund_sends_the_reembolso_metadata_as_a_form_field() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": RE_STRIPE, "status": "pending"})

    _stripe(handler).create_refund(
        PAYMENT_INTENT, 2500, f"refund-{REEMBOLSO}", {"reembolso_id": REEMBOLSO}
    )

    assert parse_qs(seen["body"])["metadata[reembolso_id]"] == [REEMBOLSO]


def test_get_refund_reads_one_refund_by_id_and_returns_its_status_and_metadata() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={"id": RE_STRIPE, "status": "succeeded", "metadata": {"reembolso_id": REEMBOLSO}},
        )

    refund = _stripe(handler).get_refund(RE_STRIPE)

    assert seen == {"method": "GET", "path": f"{REFUNDS_PATH}/{RE_STRIPE}"}
    assert refund.status == "succeeded"
    assert refund.metadata == {"reembolso_id": REEMBOLSO}


def test_list_refunds_follows_the_pages_of_one_payment_intent() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "starting_after" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "re_page_1", "status": "pending", "metadata": {}}],
                    "has_more": True,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": RE_STRIPE,
                        "status": "succeeded",
                        "metadata": {"reembolso_id": REEMBOLSO},
                    }
                ],
                "has_more": False,
            },
        )

    refunds = _stripe(handler).list_refunds_for_payment_intent(PAYMENT_INTENT)

    assert [refund.id for refund in refunds] == ["re_page_1", RE_STRIPE]
    assert refunds[1].metadata == {"reembolso_id": REEMBOLSO}
    assert requests[0].url.params["payment_intent"] == PAYMENT_INTENT
    assert requests[1].url.params["starting_after"] == "re_page_1"


def test_create_refund_without_metadata_keeps_its_previous_form_body() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": RE_STRIPE, "status": "pending"})

    _stripe(handler).create_refund(PAYMENT_INTENT, 2500, "refund-previous")

    assert parse_qs(seen["body"]) == {"payment_intent": [PAYMENT_INTENT], "amount": ["2500"]}


# ---------- worker ----------


def test_help_prints_the_module_docstring_and_never_reads_settings(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def must_not_read_settings():
        raise AssertionError("--help must not read settings or open a database")

    monkeypatch.setattr(reconcile_refunds, "Settings", must_not_read_settings)
    monkeypatch.setattr(sys, "argv", ["reconcile_refunds", "--help"])

    assert reconcile_refunds.main() == 0
    assert "python -m medicalbooking.workers.reconcile_refunds" in capsys.readouterr().out


def test_without_a_stripe_key_the_worker_exits_1_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_database(*_args, **_kwargs):
        raise AssertionError("no database must be opened without a Stripe key")

    monkeypatch.setattr(
        reconcile_refunds, "Settings", lambda: types.SimpleNamespace(stripe_secret_key=None)
    )
    monkeypatch.setattr(reconcile_refunds, "create_database_engine", no_database)
    monkeypatch.setattr(sys, "argv", ["reconcile_refunds"])

    assert reconcile_refunds.main() == 1

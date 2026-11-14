"""Refund reconciliation against real SQL Server. Stripe is a stateful double behind
httpx.MockTransport: it remembers refunds, replays a keyed create, and answers GET and list.
No request leaves the process.

Each test seeds its own patient, appointment, invoice and payment with unique identifiers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.infrastructure.stripe_checkout import (
    STRIPE_API_BASE,
    StripeCheckoutGateway,
)
from medicalbooking.workers.reconcile_refunds import build_service
from tests.integration.test_refund_request_api import Seed, _seed

pytestmark = pytest.mark.integration


class StripeRefundsDouble:
    """Remembers refunds the way Stripe does: created once per Idempotency-Key, read by id,
    listed by payment intent. Every call is recorded."""

    def __init__(self, *, create_status: str = "pending") -> None:
        self.create_status = create_status
        self.refunds: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.creates: list[httpx.Request] = []

    def add(self, refund_id: str, status: str, payment_intent: str, reembolso_id: str) -> None:
        self.refunds[refund_id] = {
            "id": refund_id,
            "status": status,
            "payment_intent": payment_intent,
            "metadata": {"reembolso_id": reembolso_id},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/refunds":
            return self._create(request)
        if request.method == "GET" and path == "/v1/refunds":
            intent = request.url.params.get("payment_intent")
            data = [
                _public(refund)
                for refund in self.refunds.values()
                if refund["payment_intent"] == intent
            ]
            return httpx.Response(200, json={"object": "list", "data": data, "has_more": False})
        if request.method == "GET" and path.startswith("/v1/refunds/"):
            refund = self.refunds.get(path.rsplit("/", 1)[-1])
            if refund is None:
                return httpx.Response(404, json={"error": {"code": "resource_missing"}})
            return httpx.Response(200, json=_public(refund))
        return httpx.Response(500, json={"error": "unexpected call in the double"})

    def _create(self, request: httpx.Request) -> httpx.Response:
        self.creates.append(request)
        key = request.headers["Idempotency-Key"]
        if key in self.by_key:
            return httpx.Response(200, json=_public(self.refunds[self.by_key[key]]))
        form = parse_qs(request.content.decode())
        refund_id = f"re_test_{uuid.uuid4().hex[:16]}"
        self.add(
            refund_id,
            self.create_status,
            form["payment_intent"][0],
            form["metadata[reembolso_id]"][0],
        )
        self.by_key[key] = refund_id
        return httpx.Response(200, json=_public(self.refunds[refund_id]))


def _public(refund: dict) -> dict:
    return {"id": refund["id"], "status": refund["status"], "metadata": refund["metadata"]}


@pytest.fixture
def double() -> StripeRefundsDouble:
    return StripeRefundsDouble()


def _gateway(double: StripeRefundsDouble) -> StripeCheckoutGateway:
    return StripeCheckoutGateway(
        secret_key="sk_test_reconcile_double",
        return_url="",
        client=httpx.Client(
            base_url=STRIPE_API_BASE, transport=httpx.MockTransport(double.handler)
        ),
        sleep=lambda _seconds: None,
    )


def _pending_reembolso(engine: Engine, seed: Seed, *, minutes_ago: int) -> str:
    """A PENDIENTE refund with no Stripe id, created `minutes_ago` minutes before now."""
    reembolso = str(uuid.uuid4())
    solicitado = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reembolsos (id, pago_id, monto, estado, motivo, solicitado_en) "
                "VALUES (:id, :p, 25.00, 'PENDIENTE', 'CANCELACION_PACIENTE', :s)"
            ),
            {"id": reembolso, "p": seed.pago_id, "s": solicitado},
        )
    return reembolso


def _reembolso(engine: Engine, reembolso_id: str) -> tuple[str, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT estado, stripe_refund_id FROM reembolsos WHERE id = :id"),
            {"id": reembolso_id},
        ).one()
    return row.estado, row.stripe_refund_id


def _factura_estado(engine: Engine, pago_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT f.estado FROM facturas AS f JOIN pagos AS p ON p.factura_id = f.id "
                "WHERE p.id = :p"
            ),
            {"p": pago_id},
        ).scalar_one()


def _reconcile_events(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM stripe_eventos WHERE tipo_evento = 'refund.reconciled'")
        ).scalar_one()


def _run(engine: Engine, double: StripeRefundsDouble):
    return build_service(engine, _gateway(double)).run()


def test_a_refund_the_provider_holds_is_recorded_and_processed(engine, double) -> None:
    seed = _seed(engine)
    reembolso = _pending_reembolso(engine, seed, minutes_ago=20)
    double.add("re_test_held_0001", "succeeded", seed.payment_intent, reembolso)

    _run(engine, double)

    assert _reembolso(engine, reembolso) == ("PROCESADO", "re_test_held_0001")
    assert _factura_estado(engine, seed.pago_id) == "REEMBOLSADA"
    assert double.creates == [], "a refund the provider holds is never created twice"


def test_a_refund_the_provider_never_saw_is_resent_with_the_same_key_and_gets_its_id(
    engine, double
) -> None:
    seed = _seed(engine)
    reembolso = _pending_reembolso(engine, seed, minutes_ago=20)

    _run(engine, double)

    [create] = double.creates
    assert create.headers["Idempotency-Key"] == f"refund-{reembolso}"
    assert parse_qs(create.content.decode())["metadata[reembolso_id]"] == [reembolso]
    stripe_id = _reembolso(engine, reembolso)[1]
    assert stripe_id is not None and stripe_id.startswith("re_test_")
    assert _reembolso(engine, reembolso)[0] == "PENDIENTE"


def test_running_the_reconciler_twice_changes_nothing(engine, double) -> None:
    seed = _seed(engine)
    processed = _pending_reembolso(engine, seed, minutes_ago=20)
    double.add("re_test_twice_0001", "succeeded", seed.payment_intent, processed)
    _run(engine, double)
    snapshot = (_reembolso(engine, processed), _factura_estado(engine, seed.pago_id))
    events = _reconcile_events(engine)

    _run(engine, double)

    assert (_reembolso(engine, processed), _factura_estado(engine, seed.pago_id)) == snapshot
    assert _reconcile_events(engine) == events


def test_a_refund_still_pending_at_the_provider_is_not_touched_on_a_second_run(
    engine, double
) -> None:
    seed = _seed(engine)
    reembolso = _pending_reembolso(engine, seed, minutes_ago=20)
    _run(engine, double)
    snapshot = _reembolso(engine, reembolso)

    _run(engine, double)

    assert _reembolso(engine, reembolso) == snapshot
    assert len(double.creates) == 1, "the second run reads the stored id and never resends"


def test_a_refund_younger_than_fifteen_minutes_is_left_alone(engine, double) -> None:
    seed = _seed(engine)
    recent = _pending_reembolso(engine, seed, minutes_ago=5)

    _run(engine, double)

    assert _reembolso(engine, recent) == ("PENDIENTE", None)
    assert double.creates == []

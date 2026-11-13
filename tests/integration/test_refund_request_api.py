"""Refund request against real SQL Server. Stripe is replaced by the real gateway class over an
httpx.MockTransport, so no request leaves the process.

Each test seeds its own patient, appointment, invoice and payment with unique identifiers.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from flask import Flask
from flask.testing import FlaskClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.payments.application.refund_service import RefundService
from medicalbooking.modules.payments.infrastructure.refund_repositories import SqlRefundStore
from medicalbooking.modules.payments.infrastructure.stripe_checkout import (
    STRIPE_API_BASE,
    StripeCheckoutGateway,
)
from medicalbooking.modules.payments.presentation.routes import REFUNDS_EXTENSION
from tests.integration.test_scheduling_api import (
    _service,
    _slot,
    _token,
    _user,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"
PROVIDER_SECRET_MARKER = "PROVIDER_BODY_MUST_NOT_LEAK"


class ProviderDouble:
    """Answers Stripe's refund endpoint through httpx.MockTransport and records every call."""

    def __init__(
        self,
        *,
        status: int = 200,
        refund_status: str = "pending",
        transport_error: type[httpx.HTTPError] | None = None,
    ) -> None:
        self.status = status
        self.refund_status = refund_status
        self.transport_error = transport_error
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.transport_error is not None:
            raise self.transport_error("provider did not answer", request=request)
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": PROVIDER_SECRET_MARKER})
        return httpx.Response(
            200,
            json={
                "id": f"re_test_{uuid.uuid4().hex[:12]}",
                "object": "refund",
                "status": self.refund_status,
            },
        )


def _gateway(double: ProviderDouble) -> StripeCheckoutGateway:
    return StripeCheckoutGateway(
        secret_key="sk_test_refund_double",
        return_url="",
        client=httpx.Client(
            base_url=STRIPE_API_BASE, transport=httpx.MockTransport(double.handler)
        ),
        sleep=lambda _seconds: None,
    )


def _install(app: Flask, engine: Engine, double: ProviderDouble) -> None:
    app.extensions[REFUNDS_EXTENSION] = RefundService(
        store=SqlRefundStore(engine),
        gateway=_gateway(double),
        clock=lambda: datetime.now(UTC),
    )


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    # Explicit: a developer's .env may hold a Stripe key. Provider calls go through the double.
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


@dataclass(frozen=True)
class Seed:
    paciente_user: str
    paciente_id: str
    pago_id: str
    cita_id: str
    payment_intent: str | None


def _seed(
    engine: Engine,
    *,
    paciente_user: str | None = None,
    pago_estado: str = "CONFIRMADO",
    estado_cita: str = "CANCELADA",
    cancelada_tras_inicio: bool = False,
    sin_cancelacion: bool = False,
    dias_desde_pago: int = 0,
    atendida: bool = False,
    con_payment_intent: bool = True,
) -> Seed:
    """A paid appointment for a patient. Defaults describe a request that is eligible."""
    usuario = paciente_user or _user(engine, "PACIENTE")
    horario = _slot(engine, days_ahead=5)
    servicio = _service(engine)
    cita, factura, pago = (str(uuid.uuid4()) for _ in range(3))
    pi = f"pi_test_{uuid.uuid4().hex[:16]}" if con_payment_intent else None
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as conn:
        paciente = conn.execute(
            text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": usuario}
        ).scalar_one()
        inicio = conn.execute(
            text("SELECT inicio_utc FROM horarios_disponibles WHERE id = :h"), {"h": horario}
        ).scalar_one()
        if sin_cancelacion:
            cancelada = None
        elif cancelada_tras_inicio:
            cancelada = inicio + timedelta(days=1)
        else:
            cancelada = now
        conn.execute(
            text(
                "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                "reservada_en, cancelada_en, clave_idempotencia) VALUES (:c, :p, :h, :s, :e, "
                ":r, :x, :k)"
            ),
            {
                "c": cita,
                "p": paciente,
                "h": horario,
                "s": servicio,
                "e": estado_cita,
                "r": now,
                "x": cancelada,
                "k": f"refund-seed-{cita}",
            },
        )
        if atendida:
            conn.execute(
                text(
                    "INSERT INTO consultas_medicas (id, cita_id, inicio_atencion) "
                    "VALUES (:id, :c, :i)"
                ),
                {"id": str(uuid.uuid4()), "c": cita, "i": inicio},
            )
        conn.execute(
            text(
                "INSERT INTO facturas (id, paciente_id, cita_id, subtotal, impuestos, total, "
                "moneda, estado, creada_en) VALUES (:f, :p, :c, 25.00, 0, 25.00, 'USD', "
                "'PAGADA', :n)"
            ),
            {"f": factura, "p": paciente, "c": cita, "n": now},
        )
        conn.execute(
            text(
                "INSERT INTO pagos (id, factura_id, stripe_payment_intent_id, clave_idempotencia, "
                "monto, moneda, estado, creado_en, pagado_en) VALUES (:p, :f, :pi, :k, 25.00, "
                "'USD', :e, :n, :pagado)"
            ),
            {
                "p": pago,
                "f": factura,
                "pi": pi,
                "k": f"refund-seed-pago-{pago}",
                "e": pago_estado,
                "n": now,
                "pagado": now - timedelta(days=dias_desde_pago),
            },
        )
    return Seed(usuario, str(paciente).lower(), pago, cita, pi)


def _body(motivo: str = "CANCELACION_PACIENTE") -> dict[str, str]:
    return {"motivo_codigo": motivo}


def _request(
    client: FlaskClient,
    headers: dict[str, str],
    pago_id: str,
    key: str,
    motivo: str = "CANCELACION_PACIENTE",
):
    return client.post(
        f"/api/v1/payments/{pago_id}/refunds",
        json=_body(motivo),
        headers={**headers, "Idempotency-Key": key},
    )


def _refunds_for(engine: Engine, pago_id: str) -> list[tuple[str, str, str | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, estado, stripe_refund_id FROM reembolsos WHERE pago_id = :p "
                "ORDER BY solicitado_en"
            ),
            {"p": pago_id},
        ).all()
    return [(str(row.id).lower(), row.estado, row.stripe_refund_id) for row in rows]


def _patient_headers(app: Flask, engine: Engine, seed: Seed) -> dict[str, str]:
    return _token(app, seed.paciente_user)


# ---------- accepted request ----------


def test_eligible_request_is_accepted_stored_and_sent_once_to_the_provider(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)
    headers = _patient_headers(app, engine, seed)

    response = _request(app.test_client(), headers, seed.pago_id, f"refund-{uuid.uuid4()}")

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["pago_id"] == seed.pago_id
    assert body["estado"] == "PENDIENTE"
    assert body["monto"] == "25.00"
    [(reembolso_id, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert reembolso_id == body["id"]
    assert estado == "PENDIENTE"
    assert stripe_id is not None and stripe_id.startswith("re_test_")
    assert len(double.calls) == 1
    sent = parse_qs(double.calls[0].content.decode())
    assert sent["payment_intent"] == [seed.payment_intent]
    assert sent["amount"] == ["2500"]
    assert double.calls[0].headers["Idempotency-Key"] == f"refund-{reembolso_id}"


# ---------- refusals ----------


REFUSALS = {
    "PAGO_NO_CONFIRMADO": {"pago_estado": "PENDIENTE"},
    "CITA_NO_CANCELADA": {"estado_cita": "CONFIRMADA", "sin_cancelacion": True},
    "CONSULTA_ATENDIDA": {"atendida": True},
    "CANCELACION_TARDIA": {"cancelada_tras_inicio": True},
    "FUERA_DE_VENTANA": {"dias_desde_pago": 31},
}


@pytest.mark.parametrize(("code", "seed_kwargs"), REFUSALS.items())
def test_each_refusal_answers_409_with_its_code_and_changes_nothing(
    app, engine, code, seed_kwargs
) -> None:
    seed = _seed(engine, **seed_kwargs)
    double = ProviderDouble()
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 409
    assert response.mimetype == "application/problem+json"
    assert response.get_json()["detail"] == code
    assert _refunds_for(engine, seed.pago_id) == []
    assert double.calls == []


def test_too_many_recent_requests_is_refused_with_409(app, engine) -> None:
    seed = _seed(engine)
    for _ in range(2):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO reembolsos (id, pago_id, monto, estado, solicitado_en) "
                    "VALUES (:id, :p, 25.00, 'FALLIDO', SYSUTCDATETIME())"
                ),
                {"id": str(uuid.uuid4()), "p": seed.pago_id},
            )
    double = ProviderDouble()
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "DEMASIADAS_SOLICITUDES"
    assert double.calls == []


def test_an_unknown_reason_code_is_refused_with_409(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble()
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
        motivo="PORQUE_QUIERO",
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "MOTIVO_INVALIDO"
    assert double.calls == []


def test_a_second_request_for_the_same_payment_is_refused(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble()
    _install(app, engine, double)
    client = app.test_client()
    headers = _patient_headers(app, engine, seed)
    assert _request(client, headers, seed.pago_id, f"refund-{uuid.uuid4()}").status_code == 201

    second = _request(client, headers, seed.pago_id, f"refund-{uuid.uuid4()}")

    assert second.status_code == 409
    assert second.get_json()["detail"] == "REEMBOLSO_EXISTENTE"
    assert len(_refunds_for(engine, seed.pago_id)) == 1
    assert len(double.calls) == 1


# ---------- ownership ----------


def test_a_foreign_patient_gets_the_same_404_as_a_missing_payment(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble()
    _install(app, engine, double)
    intruder = _token(app, _user(engine, "PACIENTE"))
    client = app.test_client()

    foreign = _request(client, intruder, seed.pago_id, f"refund-{uuid.uuid4()}")
    missing = _request(client, intruder, str(uuid.uuid4()), f"refund-{uuid.uuid4()}")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.get_json()["detail"] == missing.get_json()["detail"]
    assert _refunds_for(engine, seed.pago_id) == []
    assert double.calls == []


# ---------- payment intent and provider ----------


def test_a_payment_without_payment_intent_is_refused_before_any_provider_call(app, engine) -> None:
    seed = _seed(engine, con_payment_intent=False)
    double = ProviderDouble()
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 409
    assert response.get_json()["detail"] == "SIN_PAYMENT_INTENT"
    assert double.calls == []
    [(_, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "FALLIDO"
    assert stripe_id is None


def test_an_explicit_stripe_refusal_marks_the_refund_failed_and_answers_502(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble(status=400)
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 502
    assert response.mimetype == "application/problem+json"
    assert PROVIDER_SECRET_MARKER not in response.get_data(as_text=True)
    assert len(double.calls) == 1, "a refusal is not retried automatically"
    [(_, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "FALLIDO"
    assert stripe_id is None


def test_a_provider_5xx_has_an_unknown_outcome_and_stays_pending(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble(status=500)
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 502
    assert response.mimetype == "application/problem+json"
    assert PROVIDER_SECRET_MARKER not in response.get_data(as_text=True)
    assert len(double.calls) == 1, "an unknown outcome is not retried automatically"
    [(_, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "PENDIENTE"
    assert stripe_id is None


@pytest.mark.parametrize("transport_error", [httpx.ReadTimeout, httpx.ConnectError])
def test_a_transport_failure_after_the_post_has_an_unknown_outcome(
    app, engine, transport_error
) -> None:
    seed = _seed(engine)
    double = ProviderDouble(transport_error=transport_error)
    _install(app, engine, double)

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 502
    assert response.mimetype == "application/problem+json"
    assert len(double.calls) == 1, "a transport failure is not retried automatically"
    [(_, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "PENDIENTE"
    assert stripe_id is None


def test_a_replay_after_an_unknown_outcome_does_not_call_the_provider_again(app, engine) -> None:
    seed = _seed(engine)
    double = ProviderDouble(transport_error=httpx.ReadTimeout)
    _install(app, engine, double)
    client = app.test_client()
    headers = _patient_headers(app, engine, seed)
    key = f"refund-{uuid.uuid4()}"

    first = _request(client, headers, seed.pago_id, key)
    replay = _request(client, headers, seed.pago_id, key)

    assert first.status_code == 502
    assert replay.status_code == 200
    assert replay.get_json()["estado"] == "PENDIENTE"
    assert len(double.calls) == 1
    assert len(_refunds_for(engine, seed.pago_id)) == 1


@pytest.mark.parametrize(
    ("provider_status", "expected_estado"),
    [("failed", "FALLIDO"), ("canceled", "FALLIDO"), ("succeeded", "PENDIENTE")],
)
def test_the_provider_status_decides_the_stored_state(
    app, engine, provider_status, expected_estado
) -> None:
    seed = _seed(engine)
    _install(app, engine, ProviderDouble(refund_status=provider_status))

    response = _request(
        app.test_client(),
        _patient_headers(app, engine, seed),
        seed.pago_id,
        f"refund-{uuid.uuid4()}",
    )

    assert response.status_code == 201
    assert response.get_json()["estado"] == expected_estado
    [(_, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == expected_estado
    assert stripe_id is not None


# ---------- replay ----------


def test_the_same_key_and_body_replay_the_same_refund_without_a_second_provider_call(
    app, engine
) -> None:
    seed = _seed(engine)
    double = ProviderDouble()
    _install(app, engine, double)
    client = app.test_client()
    headers = _patient_headers(app, engine, seed)
    key = f"refund-{uuid.uuid4()}"

    first = _request(client, headers, seed.pago_id, key)
    replay = _request(client, headers, seed.pago_id, key)

    assert first.status_code == 201, first.get_json()
    assert replay.status_code == 200
    assert replay.get_json()["id"] == first.get_json()["id"]
    assert len(double.calls) == 1
    assert len(_refunds_for(engine, seed.pago_id)) == 1

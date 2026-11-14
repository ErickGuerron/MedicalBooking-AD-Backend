"""Shared helpers for checkout integration tests: ids, a seeded invoice, a failing provider."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.application.billing_service import (
    CheckoutRequest,
    CheckoutSession,
    ProviderUnavailable,
)
from tests.integration import test_billing_api as billing_api
from tests.integration.test_billing_api import StubGateway, _billing


def new_id() -> str:
    return str(uuid.uuid4())


def seed_factura(conn: Connection) -> str:
    """A pending invoice for a fresh patient, inserted directly (no appointment needed)."""
    usuario, paciente, factura = new_id(), new_id(), new_id()
    conn.execute(
        text(
            "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
            "VALUES (:id, :email, N'Nombre', N'Apellido', SYSUTCDATETIME(), SYSUTCDATETIME())"
        ),
        {"id": usuario, "email": f"{usuario}@example.test"},
    )
    conn.execute(
        text(
            "INSERT INTO pacientes (id, usuario_id, creado_en) VALUES (:id, :u, SYSUTCDATETIME())"
        ),
        {"id": paciente, "u": usuario},
    )
    conn.execute(
        text(
            "INSERT INTO facturas (id, paciente_id, subtotal, impuestos, total, moneda, estado, "
            "creada_en) VALUES (:id, :p, 25.50, 0, 25.50, 'USD', 'PENDIENTE', SYSUTCDATETIME())"
        ),
        {"id": factura, "p": paciente},
    )
    return factura


class FlakyGateway(StubGateway):
    """Fails while `failing` is true, like Stripe during an outage. Records every create."""

    def __init__(self) -> None:
        super().__init__()
        self.failing = True

    def create_session(self, request: CheckoutRequest) -> CheckoutSession:
        self.requests.append(request)
        if self.failing:
            raise ProviderUnavailable("provider down")
        session_id = f"cs_test_{uuid.uuid4().hex[:12]}"
        return CheckoutSession(id=session_id, client_secret=f"{session_id}_secret")


@contextlib.contextmanager
def failing_checkout_provider(app: Flask, engine: Engine) -> Iterator[FlakyGateway]:
    """Route the app's billing through a FlakyGateway for the duration of a test."""
    gateway = FlakyGateway()
    previous = app.extensions.get("medicalbooking.billing")
    app.extensions["medicalbooking.billing"] = _billing(engine, gateway)
    try:
        yield gateway
    finally:
        if previous is not None:
            app.extensions["medicalbooking.billing"] = previous


def invoiced_appointment(app: Flask, engine: Engine) -> tuple[dict[str, str], FlaskClient, str]:
    """A booked appointment's invoice, created through the API: (headers, client, factura)."""
    headers, cita = billing_api._booked_appointment(app, engine)
    client = app.test_client()
    response = client.post("/api/v1/invoices", json={"cita_id": cita}, headers=headers)
    return headers, client, response.get_json()["id"]

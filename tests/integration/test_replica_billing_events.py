"""Billing reaches the replica through the outbox: invoices and payments (kafka-replication S10).

The patient reads invoices and payments during an outage, so each change emits the snapshot
the replica projects, in the same SQL Server transaction. Gateway identifiers and idempotency
keys are never projected.
"""

import json
import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_billing_api import _booked_appointment
from tests.integration.test_replica_payment_events import _checkout

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(
        update={"jwt_signing_key": SecretStr(KEY), "stripe_secret_key": None}
    )
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _event(engine: Engine, aggregate_type: str, aggregate_id: str, event: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox WHERE agregado_tipo = :t "
                "AND agregado_id = :a AND tipo_evento = :e"
            ),
            {"t": aggregate_type, "a": aggregate_id, "e": event},
        ).one()
    return json.loads(row.payload)


def test_creating_an_invoice_emits_the_invoice_and_its_detail_line(app, engine) -> None:
    _headers, cita = _booked_appointment(app, engine)
    response = app.test_client().post(
        "/api/v1/invoices", json={"cita_id": cita}, headers=_headers
    )
    assert response.status_code == 201, response.get_json()
    factura_id = response.get_json()["id"]

    payload = _event(engine, "factura", factura_id, "factura.creada")

    factura = payload["factura"]
    assert factura["id"] == factura_id.lower()
    assert factura["cita_id"] == cita.lower()
    assert factura["estado"] == "PENDIENTE"
    assert factura["moneda"]
    assert factura["total"] is not None
    assert len(payload["detalles"]) == 1
    assert payload["detalles"][0]["precio_unitario"] == factura["subtotal"]


def test_the_invoice_snapshot_has_no_gateway_identifiers_or_contact_data(app, engine) -> None:
    _headers, cita = _booked_appointment(app, engine)
    factura_id = app.test_client().post(
        "/api/v1/invoices", json={"cita_id": cita}, headers=_headers
    ).get_json()["id"]

    serialized = json.dumps(_event(engine, "factura", factura_id, "factura.creada")).lower()

    assert "stripe" not in serialized
    assert "@" not in serialized
    assert "clave_idempotencia" not in serialized


def test_the_invoice_patient_is_the_patient_of_the_appointment(app, engine) -> None:
    _headers, cita = _booked_appointment(app, engine)
    factura_id = app.test_client().post(
        "/api/v1/invoices", json={"cita_id": cita}, headers=_headers
    ).get_json()["id"]

    payload = _event(engine, "factura", factura_id, "factura.creada")

    with engine.connect() as conn:
        paciente = conn.execute(
            text(
                "SELECT c.paciente_id FROM citas AS c WHERE c.id = :c"
            ),
            {"c": cita},
        ).scalar_one()
    assert payload["factura"]["paciente_id"] == str(paciente).lower()
    assert uuid.UUID(payload["factura"]["paciente_id"])


def test_the_payment_snapshot_keeps_its_existing_keys(app, engine) -> None:
    """Pins the pago.actualizado keys the replica projects, so the refund events cannot drift it."""
    _headers, _factura, pago = _checkout(app, engine)
    with engine.connect() as conn:
        payload = json.loads(
            conn.execute(
                text(
                    "SELECT TOP 1 payload FROM eventos_outbox WHERE agregado_tipo = 'pago' "
                    "AND agregado_id = :p AND tipo_evento = 'pago.actualizado' "
                    "ORDER BY version_agregado DESC"
                ),
                {"p": pago},
            ).scalar_one()
        )

    assert set(payload) == {"pago", "factura"}
    assert set(payload["pago"]) == {
        "id",
        "factura_id",
        "estado",
        "monto",
        "moneda",
        "creado_en",
        "pagado_en",
    }
    assert set(payload["factura"]) == {"id", "paciente_id", "estado"}

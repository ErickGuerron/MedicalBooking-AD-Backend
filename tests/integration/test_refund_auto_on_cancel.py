"""Cancelling a paid appointment requests its refund automatically (payments, refunds v1).

The cancellation commits first; the refund is requested afterwards, with its own transaction
and provider call. A refund that the rules refuse does not undo the cancellation. Only the
patient's own cancellation triggers it in this version. A cancellation never reaches the
provider twice for the same appointment.
"""

from collections.abc import Iterator

import pytest
from flask import Flask
from pydantic import SecretStr
from sqlalchemy import Engine, text

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from tests.integration.test_refund_request_api import (
    ProviderDouble,
    _install,
    _patient_headers,
    _refunds_for,
    _seed,
)

pytestmark = pytest.mark.integration

KEY = "integration-signing-key-with-32+chars"


@pytest.fixture(scope="module")
def app(migrated_engine: Engine, test_settings: Settings) -> Iterator[Flask]:
    settings = test_settings.model_copy(update={"jwt_signing_key": SecretStr(KEY)})
    yield create_app(settings)


@pytest.fixture
def engine(app: Flask) -> Engine:
    return app.extensions["medicalbooking.engine"]


def _paid_confirmed_appointment(engine: Engine):
    # A paid, still confirmed appointment: the patient may cancel it now.
    return _seed(engine, estado_cita="CONFIRMADA", sin_cancelacion=True)


def test_cancelling_a_paid_appointment_requests_its_refund_once(app, engine):
    seed = _paid_confirmed_appointment(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)
    headers = _patient_headers(app, engine, seed)

    response = app.test_client().post(
        f"/api/v1/appointments/{seed.cita_id}/cancel", headers=headers
    )

    assert response.status_code == 200, response.get_json()
    [(_reembolso, estado, stripe_id)] = _refunds_for(engine, seed.pago_id)
    assert estado == "PENDIENTE"
    assert stripe_id is not None
    assert len(double.calls) == 1


def test_a_refusal_does_not_undo_the_cancellation(app, engine):
    # Paid, but the payment is still pending: the rules refuse, the cancel still succeeds.
    seed = _seed(engine, estado_cita="CONFIRMADA", sin_cancelacion=True, pago_estado="PENDIENTE")
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)
    headers = _patient_headers(app, engine, seed)

    response = app.test_client().post(
        f"/api/v1/appointments/{seed.cita_id}/cancel", headers=headers
    )

    assert response.status_code == 200, response.get_json()
    assert _refunds_for(engine, seed.pago_id) == []
    assert double.calls == []
    with engine.connect() as conn:
        estado = conn.execute(
            text("SELECT estado FROM citas WHERE id = :c"),
            {"c": seed.cita_id},
        ).scalar_one()
    assert estado == "CANCELADA"


def test_a_second_cancel_does_not_request_a_second_refund(app, engine):
    seed = _paid_confirmed_appointment(engine)
    double = ProviderDouble(refund_status="pending")
    _install(app, engine, double)
    headers = _patient_headers(app, engine, seed)
    client = app.test_client()

    assert (
        client.post(f"/api/v1/appointments/{seed.cita_id}/cancel", headers=headers).status_code
        == 200
    )
    again = client.post(f"/api/v1/appointments/{seed.cita_id}/cancel", headers=headers)

    assert again.status_code in (403, 409)
    assert len(_refunds_for(engine, seed.pago_id)) == 1
    assert len(double.calls) == 1

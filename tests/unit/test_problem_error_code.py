"""Stable machine-readable error code in the problem envelope.

An AppError with a code adds one `code` key to the problem body. An AppError without a code
keeps the exact body every other endpoint already returns.
"""

from __future__ import annotations

import uuid

import pytest
from flask import Flask

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.shared.domain.errors import Forbidden


@pytest.fixture
def app() -> Flask:
    # Port 1 is closed; the routes below never reach the database.
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused")
    flask_app = create_app(settings)
    flask_app.config["TESTING"] = True
    return flask_app


def _add_raising_route(app: Flask, path: str, error: Forbidden) -> None:
    def view():
        raise error

    app.add_url_rule(path, endpoint=path, view_func=view)


def test_problem_body_carries_the_code_when_the_error_has_one(app) -> None:
    _add_raising_route(app, "/_coded", Forbidden("coded refusal", code="SAMPLE_CODE"))
    correlation_id = str(uuid.uuid4())

    response = app.test_client().get("/_coded", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 403
    assert response.mimetype == "application/problem+json"
    assert response.get_json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "coded refusal",
        "correlation_id": correlation_id,
        "code": "SAMPLE_CODE",
    }


def test_problem_body_without_a_code_is_exactly_the_old_shape(app) -> None:
    _add_raising_route(app, "/_plain", Forbidden("plain refusal"))
    correlation_id = str(uuid.uuid4())

    response = app.test_client().get("/_plain", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 403
    assert response.get_json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "detail": "plain refusal",
        "correlation_id": correlation_id,
    }
    assert "code" not in response.get_json()


def test_a_plain_app_error_has_no_code_attribute_value(app) -> None:
    assert Forbidden("plain").code is None

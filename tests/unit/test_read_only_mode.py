"""While SQL Server is unreachable, the API answers 503 READ_ONLY_MODE instead of a generic 500.

Writes get an explicit message that actions are not available in read-only mode; reads get a
message that the information is temporarily unavailable. Nothing from the driver error leaks.
"""

from __future__ import annotations

import pytest
from flask import Flask
from sqlalchemy.exc import OperationalError

from medicalbooking.app import create_app
from medicalbooking.config import Settings


def _app() -> Flask:
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused")
    app = create_app(settings)
    app.config["TESTING"] = False

    def _unreachable() -> None:
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("08S01 [Microsoft][ODBC] connection to melido-server:1433 lost"),
        )

    app.add_url_rule("/probe-read", "probe_read", _unreachable, methods=["GET"])
    app.add_url_rule("/probe-write", "probe_write", _unreachable, methods=["POST", "PATCH"])
    return app


@pytest.mark.parametrize("method", ["post", "patch"])
def test_a_write_during_an_outage_is_refused_as_read_only(method: str) -> None:
    client = _app().test_client()

    response = getattr(client, method)("/probe-write")

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "READ_ONLY_MODE"
    assert "not available in read-only mode" in body["detail"]


def test_a_read_during_an_outage_says_the_information_is_unavailable() -> None:
    response = _app().test_client().get("/probe-read")

    assert response.status_code == 503
    body = response.get_json()
    assert body["code"] == "READ_ONLY_MODE"
    assert "temporarily unavailable" in body["detail"]


def test_the_driver_message_never_reaches_the_client() -> None:
    client = _app().test_client()

    response = client.get("/probe-read")

    assert "melido-server" not in response.get_data(as_text=True)
    assert "08S01" not in response.get_data(as_text=True)

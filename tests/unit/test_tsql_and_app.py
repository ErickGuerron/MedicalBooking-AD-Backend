from __future__ import annotations

import uuid

import pytest

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.tsql import split_batches


def test_split_batches_on_standalone_go_lines_only() -> None:
    script = "CREATE TABLE a (x int)\nGO\nSELECT 'GO inside text'\n  go  \nSELECT 2\n"

    batches = split_batches(script)

    assert len(batches) == 3
    assert batches[0] == "CREATE TABLE a (x int)"
    assert batches[1] == "SELECT 'GO inside text'"
    assert batches[2] == "SELECT 2"


def test_split_batches_ignores_blank_batches() -> None:
    assert split_batches("\nGO\n   \nGO\n") == []


class _DownEngine:
    """Stands in for an unreachable database: connect fails at once.

    A real ODBC connection to a closed port waits for the login timeout (about 16 seconds per
    test), so the readiness branch is exercised without touching the network.
    """

    def connect(self):
        raise OSError("database unreachable")


@pytest.fixture
def unreachable_app():
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused")
    app = create_app(settings)
    app.config["TESTING"] = True
    app.extensions["medicalbooking.engine"] = _DownEngine()
    return app


def test_liveness_does_not_depend_on_database(unreachable_app) -> None:
    client = unreachable_app.test_client()

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_readiness_reports_unavailable_without_leaking_details(unreachable_app) -> None:
    client = unreachable_app.test_client()

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.get_json()
    assert body == {"status": "unavailable", "checks": {"database": "down"}}
    assert "localhost" not in response.get_data(as_text=True)


def test_unknown_route_returns_problem_json_with_correlation_id(unreachable_app) -> None:
    client = unreachable_app.test_client()
    correlation_id = str(uuid.uuid4())

    response = client.get("/does-not-exist", headers={"X-Correlation-ID": correlation_id})

    assert response.status_code == 404
    assert response.mimetype == "application/problem+json"
    body = response.get_json()
    assert body["status"] == 404
    assert body["correlation_id"] == correlation_id
    assert response.headers["X-Correlation-ID"] == correlation_id


def test_invalid_correlation_id_is_replaced_by_generated_one(unreachable_app) -> None:
    client = unreachable_app.test_client()

    response = client.get("/health/live", headers={"X-Correlation-ID": "not-a-uuid"})

    generated = response.headers["X-Correlation-ID"]
    assert generated != "not-a-uuid"
    uuid.UUID(generated)

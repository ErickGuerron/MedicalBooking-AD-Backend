"""Contract checks for the OpenAPI document and the Scalar reference page.

The document covers identity (sign in, current user, user creation) and scheduling
(availability, booking, cancellation). No database is needed; the schemas come from the
same Pydantic models that validate requests, so the contract cannot drift from the code.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.presentation.routes import CreateUserRequest

EXPECTED_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/me",
    "/api/v1/users",
    "/api/v1/availability",
    "/api/v1/appointments",
    "/api/v1/appointments/{appointment_id}/cancel",
}


@pytest.fixture
def client():
    # Port 1 is closed; the docs endpoints must not touch the database.
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused")
    return create_app(settings).test_client()


@pytest.fixture
def spec(client) -> dict:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    return response.get_json()


def test_document_is_openapi_31_and_covers_identity_and_scheduling(spec) -> None:
    assert spec["openapi"].startswith("3.1")
    assert set(spec["paths"]) == EXPECTED_PATHS
    assert set(spec["paths"]["/api/v1/auth/login"]) == {"post"}
    assert set(spec["paths"]["/api/v1/me"]) == {"get"}
    assert set(spec["paths"]["/api/v1/users"]) == {"post"}
    assert set(spec["paths"]["/api/v1/availability"]) == {"get"}
    assert set(spec["paths"]["/api/v1/appointments"]) == {"post"}
    assert set(spec["paths"]["/api/v1/appointments/{appointment_id}/cancel"]) == {"post"}


def test_booking_declares_the_idempotency_key_header(spec) -> None:
    operation = spec["paths"]["/api/v1/appointments"]["post"]
    headers = [param for param in operation["parameters"] if param["in"] == "header"]

    assert [header["name"] for header in headers] == ["Idempotency-Key"]
    assert headers[0]["required"] is True


def test_user_creation_schema_is_generated_from_the_validating_model(spec) -> None:
    schema = spec["components"]["schemas"]["CreateUserRequest"]

    assert schema == CreateUserRequest.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["password"]["minLength"] == 12


def test_protected_operations_require_bearer_and_login_does_not(spec) -> None:
    protected = [
        ("/api/v1/users", "post"),
        ("/api/v1/me", "get"),
        ("/api/v1/availability", "get"),
        ("/api/v1/appointments", "post"),
        ("/api/v1/appointments/{appointment_id}/cancel", "post"),
    ]
    for path, method in protected:
        assert spec["paths"][path][method]["security"] == [{"bearerAuth": []}], path
    assert spec["paths"]["/api/v1/auth/login"]["post"].get("security", []) == []
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


@pytest.mark.parametrize(
    ("path", "method", "codes"),
    [
        ("/api/v1/auth/login", "post", {"200", "401", "422"}),
        ("/api/v1/me", "get", {"200", "401"}),
        ("/api/v1/users", "post", {"201", "401", "403", "409", "422"}),
        ("/api/v1/availability", "get", {"200", "401", "403", "422"}),
        ("/api/v1/appointments", "post", {"200", "201", "401", "403", "404", "409", "422"}),
        ("/api/v1/appointments/{appointment_id}/cancel", "post", {"200", "401", "403", "409"}),
    ],
)
def test_operations_document_their_responses(spec, path: str, method: str, codes: set) -> None:
    assert set(spec["paths"][path][method]["responses"]) == codes


def test_error_responses_use_problem_json(spec) -> None:
    forbidden = spec["paths"]["/api/v1/users"]["post"]["responses"]["403"]

    assert "application/problem+json" in forbidden["content"]


PRODUCTION_SETTINGS = {
    "app_env": "production",
    "sqlserver_trust_server_certificate": False,
    "jwt_signing_key": SecretStr("production-signing-key-with-32-chars"),
}


def _client(**overrides):
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused", **overrides)
    return create_app(settings).test_client()


def test_docs_are_hidden_in_production_by_default() -> None:
    client = _client(**PRODUCTION_SETTINGS)

    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 404


def test_docs_can_be_enabled_explicitly_in_production() -> None:
    client = _client(**PRODUCTION_SETTINGS, api_docs_enabled=True)

    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_docs_can_be_disabled_outside_production() -> None:
    client = _client(api_docs_enabled=False)

    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 404


def test_scalar_reference_page_loads_the_document(client) -> None:
    response = client.get("/api/docs")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "Scalar.createApiReference" in body
    assert "/api/v1/openapi.json" in body

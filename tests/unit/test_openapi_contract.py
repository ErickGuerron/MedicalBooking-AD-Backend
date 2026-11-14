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
    "/api/v1/auth/register",
    "/api/v1/auth/verify-email",
    "/api/v1/me",
    "/api/v1/users",
    "/api/v1/availability",
    "/api/v1/appointments",
    "/api/v1/appointments/{appointment_id}/cancel",
    "/api/v1/appointments/{appointment_id}/cancellation-approval",
    "/api/v1/me/payment-methods",
    "/api/v1/me/payment-methods/setup-intents",
    "/api/v1/me/payment-methods/{payment_method_id}",
    "/api/v1/lab/orders",
    "/api/v1/lab/orders/{order_id}",
    "/api/v1/lab/orders/{order_id}/results",
    "/api/v1/lab/orders/{order_id}/review",
    "/api/v1/lab/orders/{order_id}/documents",
    "/api/v1/documents/{document_id}/download",
    "/api/v1/knowledge/analytes",
    "/api/v1/knowledge/analytes/{analyte_id}/references",
    "/api/v1/knowledge/references",
    "/api/v1/knowledge/references/{reference_id}/validate",
    "/api/v1/knowledge/rules",
    "/api/v1/knowledge/rules/{rule_id}/validate",
    "/api/v1/lab/orders/{order_id}/evaluate",
    "/api/v1/lab/orders/{order_id}/proposal",
    "/api/v1/lab/orders/{order_id}/sombra",
    "/api/v1/lab/orders/{order_id}/sombra/snapshots",
    "/api/v1/lab/orders/{order_id}/sombra/snapshots/{snapshot_id}/hallazgos/{hallazgo}/ediciones",
    "/api/v1/me/perfil-clinico",
    "/api/v1/clinical/proposals/{proposal_id}/reviews",
    "/api/v1/payments/webhook",
    "/api/v1/payments/{pago_id}/refunds",
    "/api/v1/invoices",
    "/api/v1/payments/checkout",
    "/api/v1/invoices/{factura_id}/checkout-status",
    "/api/v1/citas/{cita_id}/tratamientos",
    "/api/v1/tratamientos/{tratamiento_id}",
    "/api/v1/tratamientos/{tratamiento_id}/seguimientos",
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
    assert set(spec["paths"]["/api/v1/users"]) == {"get", "post"}
    assert set(spec["paths"]["/api/v1/availability"]) == {"get"}
    assert set(spec["paths"]["/api/v1/appointments"]) == {"get", "post"}
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
        (
            "/api/v1/appointments/{appointment_id}/cancel",
            "post",
            {"200", "401", "403", "409", "422"},
        ),
        (
            "/api/v1/appointments/{appointment_id}/cancellation-approval",
            "post",
            {"201", "401", "403", "404", "409", "422"},
        ),
        ("/api/v1/users", "get", {"200", "401", "403", "422"}),
        ("/api/v1/appointments", "get", {"200", "401", "403", "422"}),
        ("/api/v1/knowledge/rules", "get", {"200", "401", "403", "422"}),
        ("/api/v1/knowledge/references", "get", {"200", "401", "403", "404", "422"}),
        (
            "/api/v1/lab/orders/{order_id}/sombra/snapshots/{snapshot_id}/"
            "hallazgos/{hallazgo}/ediciones",
            "get",
            {"200", "401", "403"},
        ),
    ],
)
def test_operations_document_their_responses(spec, path: str, method: str, codes: set) -> None:
    assert set(spec["paths"][path][method]["responses"]) == codes


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/users", "get"),
        ("/api/v1/appointments", "get"),
        ("/api/v1/knowledge/rules", "get"),
        ("/api/v1/knowledge/references", "get"),
        (
            "/api/v1/lab/orders/{order_id}/sombra/snapshots/{snapshot_id}/"
            "hallazgos/{hallazgo}/ediciones",
            "get",
        ),
    ],
)
def test_read_listing_operations_are_documented_with_bearer(spec, path: str, method: str) -> None:
    assert method in spec["paths"][path]
    assert spec["paths"][path][method]["security"] == [{"bearerAuth": []}]


def test_user_listing_documents_pagination_defaults_and_bounds(spec) -> None:
    parameters = {p["name"]: p for p in spec["paths"]["/api/v1/users"]["get"]["parameters"]}

    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
        "default": 50,
    }
    assert parameters["offset"]["schema"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 2147483647,
        "default": 0,
    }


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


def test_treatment_operations_are_declared_with_bearer_auth(spec) -> None:
    assert set(spec["paths"]["/api/v1/citas/{cita_id}/tratamientos"]) == {"get", "post"}
    assert set(spec["paths"]["/api/v1/tratamientos/{tratamiento_id}"]) == {"patch"}
    assert set(spec["paths"]["/api/v1/tratamientos/{tratamiento_id}/seguimientos"]) == {
        "get",
        "post",
    }
    protected = [
        ("/api/v1/citas/{cita_id}/tratamientos", "get"),
        ("/api/v1/citas/{cita_id}/tratamientos", "post"),
        ("/api/v1/tratamientos/{tratamiento_id}", "patch"),
        ("/api/v1/tratamientos/{tratamiento_id}/seguimientos", "get"),
        ("/api/v1/tratamientos/{tratamiento_id}/seguimientos", "post"),
    ]
    for path, method in protected:
        assert spec["paths"][path][method]["security"] == [{"bearerAuth": []}], path


def test_treatment_request_schemas_forbid_unknown_fields(spec) -> None:
    from medicalbooking.modules.treatments.presentation.routes import (
        CreateTreatmentRequest,
        FollowUpRequest,
        UpdateTreatmentRequest,
    )

    schemas = spec["components"]["schemas"]
    for name, model in [
        ("CreateTreatmentRequest", CreateTreatmentRequest),
        ("UpdateTreatmentRequest", UpdateTreatmentRequest),
        ("FollowUpRequest", FollowUpRequest),
    ]:
        assert schemas[name] == model.model_json_schema(), name
        assert schemas[name]["additionalProperties"] is False, name
    assert "plan_tratamiento" not in schemas["CreateTreatmentRequest"]["properties"]


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


def test_scalar_script_is_pinned_and_protected_by_subresource_integrity(client) -> None:
    body = client.get("/api/docs").get_data(as_text=True)

    # A floating CDN URL could change under the page; a pinned version plus SRI makes the
    # browser refuse any different file.
    assert "@scalar/api-reference@1.76.4" in body
    assert 'integrity="sha384-' in body
    assert 'crossorigin="anonymous"' in body


def test_scalar_reference_page_loads_the_document(client) -> None:
    response = client.get("/api/docs")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "Scalar.createApiReference" in body
    assert "/api/v1/openapi.json" in body

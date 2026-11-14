"""OpenAPI contract for /me/payment-methods: every operation is documented with bearer security,
its status codes and its exact response fields."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from medicalbooking.app import create_app
from medicalbooking.config import Settings

CARDS = "/api/v1/me/payment-methods"
SETUP = "/api/v1/me/payment-methods/setup-intents"
REMOVE = "/api/v1/me/payment-methods/{payment_method_id}"


@pytest.fixture
def spec() -> dict:
    # Port 1 is closed; the docs endpoints must not touch the database.
    settings = Settings(
        _env_file=None,
        sqlserver_port=1,
        sqlserver_database="unused",
        api_docs_enabled=True,
        jwt_signing_key=SecretStr("openapi-test-signing-key-with-32-chars"),
    )
    response = create_app(settings).test_client().get("/api/v1/openapi.json")
    assert response.status_code == 200
    return response.get_json()


def _operation(spec: dict, path: str, method: str) -> dict:
    return spec["paths"][path][method]


@pytest.mark.parametrize(
    ("path", "method"),
    [(CARDS, "get"), (SETUP, "post"), (REMOVE, "delete")],
)
def test_card_operations_are_documented_with_bearer_security(spec, path, method) -> None:
    assert _operation(spec, path, method)["security"] == [{"bearerAuth": []}]


def test_listing_documents_the_card_fields_and_no_card_data(spec) -> None:
    response = _operation(spec, CARDS, "get")["responses"]["200"]
    schema = response["content"]["application/json"]["schema"]
    item = schema["properties"]["items"]["items"]
    assert set(item["properties"]) == {
        "id",
        "brand",
        "last4",
        "exp_month",
        "exp_year",
        "is_default",
    }
    assert item["additionalProperties"] is False


def test_listing_documents_the_limit_and_cursor_parameters(spec) -> None:
    parameters = {p["name"]: p for p in _operation(spec, CARDS, "get")["parameters"]}
    assert set(parameters) == {"limit", "starting_after"}
    assert parameters["limit"]["in"] == "query"
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 10,
    }
    assert parameters["starting_after"]["in"] == "query"
    assert parameters["starting_after"]["required"] is False


def test_listing_documents_the_page_envelope(spec) -> None:
    response = _operation(spec, CARDS, "get")["responses"]["200"]
    schema = response["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"items", "has_more", "next_cursor"}
    assert schema["properties"]["has_more"]["type"] == "boolean"


def test_setup_intent_requires_the_idempotency_key_header(spec) -> None:
    parameters = _operation(spec, SETUP, "post")["parameters"]
    assert parameters == [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "description": parameters[0]["description"],
            "schema": {"type": "string", "minLength": 8, "maxLength": 100},
        }
    ]


def test_setup_intent_documents_the_secret_and_the_publishable_key(spec) -> None:
    response = _operation(spec, SETUP, "post")["responses"]["201"]
    schema = response["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"client_secret", "publishable_key"}


@pytest.mark.parametrize(
    ("path", "method", "codes"),
    [
        (CARDS, "get", {"200", "401", "403", "422", "503"}),
        (SETUP, "post", {"201", "401", "403", "422", "503"}),
        (REMOVE, "delete", {"204", "401", "403", "503"}),
    ],
)
def test_operations_document_their_status_codes(spec, path, method, codes) -> None:
    assert set(_operation(spec, path, method)["responses"]) == codes


def test_removal_documents_the_card_id_path_parameter(spec) -> None:
    parameters = _operation(spec, REMOVE, "delete")["parameters"]
    assert {"name": "payment_method_id", "in": "path"}.items() <= parameters[0].items()
    assert parameters[0]["required"] is True

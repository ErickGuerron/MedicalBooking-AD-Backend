"""OpenAPI fragment for the administrator contract: sign in, current user, create users.

Request schemas are generated from the same Pydantic models that validate requests, so
the published contract cannot drift from the implementation.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.identity.presentation.routes import CreateUserRequest, LoginRequest

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]


def _json_body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _json_response(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def identity_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/auth/login": {
                "post": {
                    "tags": ["Identity"],
                    "operationId": "login",
                    "summary": "Sign in with email and password",
                    "description": (
                        "Returns a short-lived bearer token. Unknown emails and wrong "
                        "passwords return the same problem."
                    ),
                    "requestBody": _json_body("LoginRequest"),
                    "responses": {
                        "200": _json_response("Token issued", "LoginResponse"),
                        "401": _problem("Invalid credentials"),
                        "422": _problem("Invalid request body"),
                    },
                }
            },
            "/api/v1/me": {
                "get": {
                    "tags": ["Identity"],
                    "operationId": "getCurrentUser",
                    "summary": "Read the authenticated user, roles and permissions",
                    "security": _BEARER,
                    "responses": {
                        "200": _json_response("Authenticated user", "MeResponse"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                    },
                }
            },
            "/api/v1/users": {
                "post": {
                    "tags": ["Identity"],
                    "operationId": "createUser",
                    "summary": "Create a user account (administrator)",
                    "description": "Requires the usuarios.administrar permission.",
                    "security": _BEARER,
                    "requestBody": _json_body("CreateUserRequest"),
                    "responses": {
                        "201": _json_response("User created", "UserCreated"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks usuarios.administrar"),
                        "409": _problem("Email already registered"),
                        "422": _problem("Invalid request body"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "LoginRequest": LoginRequest.model_json_schema(),
                "CreateUserRequest": CreateUserRequest.model_json_schema(),
                "LoginResponse": {
                    "type": "object",
                    "required": ["access_token", "token_type", "expires_in"],
                    "properties": {
                        "access_token": {"type": "string"},
                        "token_type": {"type": "string", "const": "Bearer"},
                        "expires_in": {"type": "integer", "description": "Seconds"},
                    },
                },
                "MeResponse": {
                    "type": "object",
                    "required": ["id", "roles", "permissions"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "roles": {"type": "array", "items": {"type": "string"}},
                        "permissions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "UserCreated": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string", "format": "uuid"}},
                },
            }
        },
    }

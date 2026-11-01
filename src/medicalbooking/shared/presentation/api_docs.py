"""OpenAPI document assembly and the Scalar reference page.

Modules contribute fragments (paths and component schemas); this module merges them into
one document and serves it with Scalar for manual endpoint testing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from flask import Blueprint, Flask, Response, jsonify

SPEC_PATH = "/api/v1/openapi.json"
DOCS_PATH = "/api/docs"
SCALAR_SCRIPT = "https://cdn.jsdelivr.net/npm/@scalar/api-reference"

_PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["type", "title", "status", "detail", "correlation_id"],
    "properties": {
        "type": {"type": "string", "example": "about:blank"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "correlation_id": {"type": "string", "format": "uuid"},
    },
}

_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MedicalBooking API reference</title>
  </head>
  <body>
    <div id="app"></div>
    <script src="{script}"></script>
    <script>
      Scalar.createApiReference('#app', {{ url: '{spec}' }})
    </script>
  </body>
</html>
"""


def build_document(fragments: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Merge module fragments into one OpenAPI 3.1 document."""
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "MedicalBooking API",
            "version": "1.0.0",
            "description": (
                "Identity (sign in, current user, user creation) and scheduling "
                "(availability, booking with Idempotency-Key, cancellation)."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": {},
        "components": {
            "schemas": {"Problem": _PROBLEM_SCHEMA},
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            },
        },
    }
    for fragment in fragments:
        for path, operations in fragment.get("paths", {}).items():
            if path in document["paths"]:
                raise ValueError(f"duplicate OpenAPI path: {path}")
            document["paths"][path] = operations
        for name, schema in fragment.get("components", {}).get("schemas", {}).items():
            if name in document["components"]["schemas"]:
                raise ValueError(f"duplicate OpenAPI schema: {name}")
            document["components"]["schemas"][name] = schema
    return document


def register_api_docs(app: Flask, fragments: Iterable[dict[str, Any]]) -> None:
    """Serve the merged document at SPEC_PATH and the Scalar page at DOCS_PATH."""
    document = build_document(fragments)
    blueprint = Blueprint("api_docs", __name__)

    @blueprint.get(SPEC_PATH)
    def openapi_document() -> Response:
        return jsonify(document)

    @blueprint.get(DOCS_PATH)
    def api_reference() -> Response:
        page = _PAGE.format(script=SCALAR_SCRIPT, spec=SPEC_PATH)
        return Response(page, mimetype="text/html")

    app.register_blueprint(blueprint)

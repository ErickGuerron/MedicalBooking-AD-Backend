"""Flask application factory.

Each replica is stateless: no in-memory locks or caches hold business state.
Concurrency is enforced by the database.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.database import create_database_engine
from medicalbooking.shared.presentation.health import health_bp

_CORRELATION_HEADER = "X-Correlation-ID"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask("medicalbooking")
    app.extensions["medicalbooking.settings"] = settings
    app.extensions["medicalbooking.engine"] = create_database_engine(settings)

    app.register_blueprint(health_bp)
    _register_error_handlers(app)
    _register_correlation_id(app)
    return app


def _register_correlation_id(app: Flask) -> None:
    @app.before_request
    def _set_correlation_id() -> None:
        incoming = request.headers.get(_CORRELATION_HEADER, "")
        try:
            g.correlation_id = str(uuid.UUID(incoming))
        except ValueError:
            g.correlation_id = str(uuid.uuid4())

    @app.after_request
    def _echo_correlation_id(response: Response) -> Response:
        response.headers[_CORRELATION_HEADER] = g.get("correlation_id", "")
        return response


def _problem(status: int, title: str, detail: str) -> tuple[Response, int]:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": g.get("correlation_id", ""),
    }
    response = jsonify(body)
    response.mimetype = "application/problem+json"
    return response, status


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def _http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, error.name, error.description or "")

    @app.errorhandler(Exception)
    def _unexpected_error(error: Exception) -> tuple[Response, int]:
        app.logger.exception("unhandled error", extra={"correlation_id": g.get("correlation_id")})
        return _problem(500, "Internal Server Error", "Unexpected error. Use correlation_id.")

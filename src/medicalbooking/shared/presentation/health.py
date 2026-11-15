"""Liveness and readiness probes.

Liveness never touches dependencies. Readiness checks the database and reports
only a status, never connection details or clinical data.
"""

from __future__ import annotations

import socket

from flask import Blueprint, Response, current_app, jsonify
from sqlalchemy import Engine, text

from medicalbooking.config import Settings

health_bp = Blueprint("health", __name__, url_prefix="/health")


@health_bp.get("/live")
def live() -> tuple[Response, int]:
    settings: Settings = current_app.extensions["medicalbooking.settings"]
    machine = settings.machine_name or socket.gethostname()
    return jsonify(status="ok", machine=machine, api=settings.replica_id), 200


@health_bp.get("/ready")
def ready() -> tuple[Response, int]:
    engine: Engine = current_app.extensions["medicalbooking.engine"]
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means not ready; details stay in logs
        current_app.logger.exception("readiness database check failed")
        return jsonify(status="unavailable", checks={"database": "down"}), 503
    return jsonify(status="ready", checks={"database": "up"}), 200

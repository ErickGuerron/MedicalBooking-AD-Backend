"""OpenAPI fragment for the administrator reports and monitoring (read-only)."""

from __future__ import annotations

from typing import Any

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]
_DATE = {"type": "string", "format": "date"}


def reporting_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/admin/reportes/resumen": {
                "get": {
                    "summary": "Summary of appointments, lab orders, payments and treatments",
                    "operationId": "getAdminReportSummary",
                    "tags": ["Reporting"],
                    "security": _BEARER,
                    "parameters": [
                        {"name": "desde", "in": "query", "required": True, "schema": _DATE},
                        {"name": "hasta", "in": "query", "required": True, "schema": _DATE},
                    ],
                    "responses": {
                        "200": {
                            "description": "Counts by state; payments include the amount",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        },
                        "403": {
                            "description": "Missing reportes.leer",
                            "content": _PROBLEM_CONTENT,
                        },
                        "422": {
                            "description": "Invalid or too long period",
                            "content": _PROBLEM_CONTENT,
                        },
                    },
                }
            },
            "/api/v1/admin/monitoreo/estado": {
                "get": {
                    "summary": "Database latency and replication backlog per destination",
                    "operationId": "getAdminMonitoringStatus",
                    "tags": ["Reporting"],
                    "security": _BEARER,
                    "responses": {
                        "200": {
                            "description": "Monitoring snapshot",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        },
                        "403": {
                            "description": "Missing monitoreo.leer",
                            "content": _PROBLEM_CONTENT,
                        },
                    },
                }
            },
        }
    }

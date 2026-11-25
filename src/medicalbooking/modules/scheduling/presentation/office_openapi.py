"""OpenAPI fragment for sedes and consultorios administration (sedes.administrar)."""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.scheduling.presentation.openapi import (
    _BEARER,
    _json_response,
    _problem,
)

_SEDE_PARAM = {
    "name": "sede_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}
_CONSULTORIO_PARAM = {
    "name": "consultorio_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}


def _body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def office_admin_fragment() -> dict[str, Any]:
    """Sedes and consultorios: the administrator creates them and edits them."""
    return {
        "paths": {
            "/api/v1/admin/sedes": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "listSedes",
                    "summary": "List every sede with its consultorios, active or not",
                    "security": _BEARER,
                    "responses": {
                        "200": _json_response("Sedes", "SedeList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks sedes.administrar"),
                    },
                },
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "createSede",
                    "summary": "Create a sede. Every sede uses America/Guayaquil",
                    "security": _BEARER,
                    "requestBody": _body("SedeInput"),
                    "responses": {
                        "201": _json_response("Created sede", "Sede"),
                        "400": _problem("Invalid nombre or direccion"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks sedes.administrar"),
                    },
                },
            },
            "/api/v1/admin/sedes/{sede_id}": {
                "patch": {
                    "tags": ["Scheduling"],
                    "operationId": "updateSede",
                    "summary": "Edit a sede. Deactivation is refused while it has future bookings",
                    "security": _BEARER,
                    "parameters": [_SEDE_PARAM],
                    "requestBody": _body("SedeUpdate"),
                    "responses": {
                        "200": _json_response("Updated sede", "Sede"),
                        "400": _problem("Invalid field"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks sedes.administrar"),
                        "404": _problem("Sede not found"),
                        "409": _problem("Future appointments hold a consultorio of this sede"),
                    },
                },
            },
            "/api/v1/admin/sedes/{sede_id}/consultorios": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "createConsultorio",
                    "summary": "Create a consultorio in a sede",
                    "security": _BEARER,
                    "parameters": [_SEDE_PARAM],
                    "requestBody": _body("ConsultorioInput"),
                    "responses": {
                        "201": _json_response("The sede with its consultorios", "Sede"),
                        "400": _problem("Invalid nombre, or the sede is inactive"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks sedes.administrar"),
                        "404": _problem("Sede not found"),
                        "409": _problem("A consultorio with that name already exists in the sede"),
                    },
                },
            },
            "/api/v1/admin/consultorios/{consultorio_id}": {
                "patch": {
                    "tags": ["Scheduling"],
                    "operationId": "updateConsultorio",
                    "summary": "Edit a consultorio; deactivation is refused with future bookings",
                    "security": _BEARER,
                    "parameters": [_CONSULTORIO_PARAM],
                    "requestBody": _body("ConsultorioUpdate"),
                    "responses": {
                        "200": _json_response("Updated consultorio", "ConsultorioDetail"),
                        "400": _problem("Invalid field, or the sede is inactive"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks sedes.administrar"),
                        "404": _problem("Consultorio not found"),
                        "409": _problem(
                            "Future appointments, or the name already exists in the sede"
                        ),
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "ConsultorioDetail": {
                    "type": "object",
                    "required": ["id", "sede_id", "nombre", "activo"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "sede_id": {"type": "string", "format": "uuid"},
                        "nombre": {"type": "string", "maxLength": 80},
                        "activo": {"type": "boolean"},
                    },
                },
                "Sede": {
                    "type": "object",
                    "required": ["id", "nombre", "direccion", "activo", "consultorios"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "nombre": {"type": "string", "maxLength": 150},
                        "direccion": {"type": ["string", "null"], "maxLength": 300},
                        "activo": {"type": "boolean"},
                        "consultorios": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["id", "nombre", "activo"],
                                "properties": {
                                    "id": {"type": "string", "format": "uuid"},
                                    "nombre": {"type": "string", "maxLength": 80},
                                    "activo": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
                "SedeList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/Sede"}}
                    },
                },
                "SedeInput": {
                    "type": "object",
                    "required": ["nombre"],
                    "additionalProperties": False,
                    "properties": {
                        "nombre": {"type": "string", "maxLength": 150},
                        "direccion": {"type": ["string", "null"], "maxLength": 300},
                    },
                },
                "SedeUpdate": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "Only sent fields change. A null nombre or activo is refused.",
                    "properties": {
                        "nombre": {"type": "string", "maxLength": 150},
                        "direccion": {"type": ["string", "null"], "maxLength": 300},
                        "activo": {"type": "boolean"},
                    },
                },
                "ConsultorioInput": {
                    "type": "object",
                    "required": ["nombre"],
                    "additionalProperties": False,
                    "properties": {"nombre": {"type": "string", "maxLength": 80}},
                },
                "ConsultorioUpdate": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "Only sent fields change. A null nombre or activo is refused.",
                    "properties": {
                        "nombre": {"type": "string", "maxLength": 80},
                        "activo": {"type": "boolean"},
                    },
                },
            }
        },
    }

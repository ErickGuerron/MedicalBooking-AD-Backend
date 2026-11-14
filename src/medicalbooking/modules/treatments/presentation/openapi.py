"""OpenAPI fragment for treatments: a treatment per appointment and its append-only follow-ups.

Request schemas are generated from the same Pydantic models that validate requests.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.treatments.presentation.routes import (
    CreateTreatmentRequest,
    FollowUpRequest,
    UpdateTreatmentRequest,
)

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]
_UUID_PARAM = {"type": "string", "format": "uuid"}
_ESTADO = {"type": "string", "enum": ["ACTIVO", "FINALIZADO", "SUSPENDIDO"]}


def _json(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def _path_param(name: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "schema": _UUID_PARAM}


def _body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def treatments_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/citas/{cita_id}/tratamientos": {
                "get": {
                    "tags": ["Treatments"],
                    "operationId": "listCitaTreatments",
                    "summary": "List the treatments of an appointment",
                    "description": (
                        "Allowed for the treating doctor of the appointment and its owning "
                        "patient. Anyone else, or a missing appointment, gets 403."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("cita_id")],
                    "responses": {
                        "200": _json("Treatments", "TreatmentList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller may not read this appointment"),
                    },
                },
                "post": {
                    "tags": ["Treatments"],
                    "operationId": "createCitaTreatment",
                    "summary": "Create a treatment for an appointment",
                    "description": (
                        "Only the treating doctor of the appointment. The state defaults to "
                        "ACTIVO. FINALIZADO requires fecha_fin."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("cita_id")],
                    "requestBody": _body("CreateTreatmentRequest"),
                    "responses": {
                        "201": _json("Treatment created", "Treatment"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not the treating doctor of this appointment"),
                        "422": _problem("Invalid body, state or dates"),
                    },
                },
            },
            "/api/v1/tratamientos/{tratamiento_id}": {
                "patch": {
                    "tags": ["Treatments"],
                    "operationId": "updateTreatment",
                    "summary": "Update a treatment's description, state or end date",
                    "description": (
                        "Only the treating doctor. FINALIZADO is terminal and needs fecha_fin. "
                        "A patch with no fields is rejected."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("tratamiento_id")],
                    "requestBody": _body("UpdateTreatmentRequest"),
                    "responses": {
                        "200": _json("Treatment updated", "Treatment"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not the treating doctor of this treatment"),
                        "409": _problem("The treatment changed concurrently; reload and retry"),
                        "422": _problem("Invalid body, state change or dates"),
                    },
                },
            },
            "/api/v1/tratamientos/{tratamiento_id}/seguimientos": {
                "get": {
                    "tags": ["Treatments"],
                    "operationId": "listTreatmentFollowUps",
                    "summary": "List the follow-up notes of a treatment",
                    "description": (
                        "Oldest first, in registration order. Allowed for the treating doctor "
                        "and the owning patient."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("tratamiento_id")],
                    "responses": {
                        "200": _json("Follow-up notes", "FollowUpList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller may not read this treatment"),
                    },
                },
                "post": {
                    "tags": ["Treatments"],
                    "operationId": "addTreatmentFollowUp",
                    "summary": "Append a follow-up note to a treatment",
                    "description": (
                        "Append-only: follow-up notes are never edited or deleted through the API."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("tratamiento_id")],
                    "requestBody": _body("FollowUpRequest"),
                    "responses": {
                        "201": _json("Follow-up note created", "FollowUp"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not the treating doctor of this treatment"),
                        "422": _problem("Invalid or empty note"),
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "CreateTreatmentRequest": CreateTreatmentRequest.model_json_schema(),
                "UpdateTreatmentRequest": UpdateTreatmentRequest.model_json_schema(),
                "FollowUpRequest": FollowUpRequest.model_json_schema(),
                "Treatment": {
                    "type": "object",
                    "required": ["id", "cita_id", "descripcion", "estado", "fecha_inicio"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "cita_id": _UUID_PARAM,
                        "descripcion": {"type": "string"},
                        "estado": _ESTADO,
                        "fecha_inicio": {"type": "string", "format": "date"},
                        "fecha_fin": {"type": ["string", "null"], "format": "date"},
                    },
                },
                "TreatmentList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Treatment"},
                        }
                    },
                },
                "FollowUp": {
                    "type": "object",
                    "required": ["id", "tratamiento_id", "nota", "registrado_en"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "tratamiento_id": _UUID_PARAM,
                        "nota": {"type": "string"},
                        "registrado_en": {"type": "string", "format": "date-time"},
                    },
                },
                "FollowUpList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FollowUp"},
                        }
                    },
                },
            }
        },
    }

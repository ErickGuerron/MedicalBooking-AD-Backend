"""OpenAPI fragment for scheduling: availability, booking and cancellation.

The booking request schema is generated from the same Pydantic model that validates it.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.scheduling.domain.policies import APPOINTMENT_STATES
from medicalbooking.modules.scheduling.presentation.routes import BookAppointmentRequest

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]


def _json_response(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def _query(
    name: str, description: str, *, required: bool, schema: dict[str, Any]
) -> dict[str, Any]:
    return {
        "name": name,
        "in": "query",
        "required": required,
        "description": description,
        "schema": schema,
    }


def scheduling_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/availability": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "listAvailability",
                    "summary": "List free schedule slots in a time window",
                    "description": (
                        "Only slots that have not started are returned. The window can be "
                        "at most 31 days long."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        _query(
                            "desde",
                            "Window start, ISO-8601 with timezone offset",
                            required=True,
                            schema={"type": "string", "format": "date-time"},
                        ),
                        _query(
                            "hasta",
                            "Window end, ISO-8601 with timezone offset (after 'desde')",
                            required=True,
                            schema={"type": "string", "format": "date-time"},
                        ),
                        _query(
                            "medico_id",
                            "Optional: only slots of this clinician",
                            required=False,
                            schema={"type": "string", "format": "uuid"},
                        ),
                    ],
                    "responses": {
                        "200": _json_response("Free slots", "AvailabilityResponse"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks citas.reservar"),
                        "422": _problem("Invalid window or parameters"),
                    },
                }
            },
            "/api/v1/appointments": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "listAppointments",
                    "summary": "List own appointments, or the doctor's agenda",
                    "description": (
                        "A patient sees own appointments; a doctor with a doctor profile sees "
                        "appointments of his or her agenda; any other caller gets 403. Ordered "
                        "by slot start, then id. Rows carry no patient data."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "estado",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "enum": sorted(APPOINTMENT_STATES)},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 200,
                                "default": 50,
                            },
                        },
                        {
                            "name": "offset",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 0, "default": 0},
                        },
                    ],
                    "responses": {
                        "200": _json_response("Appointments", "AppointmentSummaryList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is neither a patient nor a doctor with an agenda"),
                        "422": _problem("Invalid estado, limit or offset"),
                    },
                },
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "bookAppointment",
                    "summary": "Reserve a schedule slot",
                    "description": (
                        "Idempotent: repeating the same Idempotency-Key and body returns the "
                        "original appointment (200). Reusing the key with a different body "
                        "returns 409. A new reservation returns 201."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "description": "8-100 characters: letters, digits, '.', '_', ':', '-'",
                            "schema": {
                                "type": "string",
                                "minLength": 8,
                                "maxLength": 100,
                                "pattern": "^[A-Za-z0-9._:-]{8,100}$",
                            },
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/BookAppointmentRequest"}
                            }
                        },
                    },
                    "responses": {
                        "201": _json_response(
                            "Appointment reserved (pending payment)", "Appointment"
                        ),
                        "200": _json_response(
                            "Identical retry: original appointment", "Appointment"
                        ),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller has no patient profile"),
                        "404": _problem("Schedule slot or medical service not found"),
                        "409": _problem("Slot already taken, or key reused with another request"),
                        "422": _problem("Invalid body, missing key or slot already started"),
                    },
                },
            },
            "/api/v1/appointments/{appointment_id}/cancel": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "cancelAppointment",
                    "summary": "Cancel an own appointment and release its slot",
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "appointment_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": _json_response("Appointment cancelled", "Appointment"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Appointment not found or not owned by the caller"),
                        "409": _problem("Appointment can no longer be cancelled"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "BookAppointmentRequest": BookAppointmentRequest.model_json_schema(),
                "AppointmentSummary": {
                    "type": "object",
                    "required": ["id", "estado", "inicio", "fin"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "estado": {"type": "string", "enum": sorted(APPOINTMENT_STATES)},
                        "inicio": {"type": "string", "format": "date-time"},
                        "fin": {"type": "string", "format": "date-time"},
                    },
                },
                "AppointmentSummaryList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/AppointmentSummary"},
                        }
                    },
                },
                "AvailableSlot": {
                    "type": "object",
                    "required": ["horario_id", "medico_id", "inicio_utc", "fin_utc"],
                    "properties": {
                        "horario_id": {"type": "string", "format": "uuid"},
                        "medico_id": {"type": "string", "format": "uuid"},
                        "inicio_utc": {"type": "string", "format": "date-time"},
                        "fin_utc": {"type": "string", "format": "date-time"},
                    },
                },
                "AvailabilityResponse": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/AvailableSlot"},
                        }
                    },
                },
                "Appointment": {
                    "type": "object",
                    "required": [
                        "id",
                        "estado",
                        "horario_id",
                        "servicio_id",
                        "motivo",
                        "expiracion_reserva",
                    ],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "estado": {
                            "type": "string",
                            "enum": [
                                "PENDIENTE_PAGO",
                                "CONFIRMADA",
                                "COMPLETADA",
                                "CANCELADA",
                                "EXPIRADA",
                                "NO_ASISTIO",
                            ],
                        },
                        "horario_id": {"type": "string", "format": "uuid"},
                        "servicio_id": {"type": "string", "format": "uuid"},
                        "motivo": {"type": ["string", "null"], "maxLength": 500},
                        "expiracion_reserva": {"type": ["string", "null"], "format": "date-time"},
                    },
                },
            }
        },
    }

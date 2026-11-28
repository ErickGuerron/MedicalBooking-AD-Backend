"""OpenAPI fragment for scheduling: availability, booking and cancellation.

The booking request schema is generated from the same Pydantic model that validates it.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.scheduling.domain.policies import APPOINTMENT_STATES
from medicalbooking.modules.scheduling.presentation.routes import (
    BookAppointmentRequest,
    CancelAppointmentRequest,
    GenerateSlotsRequest,
    WorkHoursRequest,
)

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
            "/api/v1/me/horario-trabajo": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "getMyWorkHours",
                    "summary": "Read the weekly hours the signed-in doctor works",
                    "security": _BEARER,
                    "responses": {
                        "200": _json_response("Declared work blocks", "WorkHoursList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not a doctor with a profile"),
                    },
                },
                "put": {
                    "tags": ["Scheduling"],
                    "operationId": "setMyWorkHours",
                    "summary": "Declare the weekly hours the signed-in doctor works",
                    "description": (
                        "Replaces the whole weekly schedule. Hours are inside the clinic's opening "
                        "hours (08:00 to 17:00), start and end on a 15-minute step, do not overlap "
                        "on the same day, and at most four blocks a day. An empty list "
                        "removes them."
                    ),
                    "security": _BEARER,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WorkHoursRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": _json_response("Saved work blocks", "WorkHoursList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not a doctor with a profile"),
                        "422": _problem("Hours outside the clinic, overlapping or off the step"),
                    },
                },
            },
            "/api/v1/admin/consultorios": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "listConsultorios",
                    "summary": "List the active consultorios where a week of slots can be created",
                    "security": _BEARER,
                    "responses": {
                        "200": _json_response("Active consultorios", "ConsultorioList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks horario.generar"),
                    },
                },
            },
            "/api/v1/admin/medicos/{medico_id}/horario-trabajo": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "getDoctorWorkHours",
                    "summary": "Read the weekly hours a doctor declared, to plan their week",
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "medico_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": {
                        "200": _json_response("Declared work blocks", "WorkHoursList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks horario.generar"),
                        "404": _problem("Doctor not found or inactive"),
                    },
                },
            },
            "/api/v1/admin/horarios/propuesta": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "proposeDoctorSlots",
                    "summary": "Propose the one-hour slots of a week from a doctor's hours",
                    "description": (
                        "Writes nothing. The week starts on a Monday, is not over yet, is at most "
                        "12 weeks ahead, and needs the doctor to have declared hours."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        _query(
                            "medico_id",
                            "Doctor",
                            required=True,
                            schema={"type": "string", "format": "uuid"},
                        ),
                        _query(
                            "semana",
                            "Monday that starts the week, YYYY-MM-DD",
                            required=True,
                            schema={"type": "string", "format": "date"},
                        ),
                    ],
                    "responses": {
                        "200": _json_response("Proposed slots", "ProposedSlotList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks horario.generar"),
                        "404": _problem("Doctor not found or inactive"),
                        "409": _problem("The doctor has not declared work hours"),
                        "422": _problem("Invalid range or dates"),
                    },
                },
            },
            "/api/v1/admin/horarios/generar": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "generateDoctorSlots",
                    "summary": "Create the one-hour slots of a week, in one consultorio",
                    "description": (
                        "Idempotent: a slot the doctor already has at the same start is counted as "
                        "existing and is not created again."
                    ),
                    "security": _BEARER,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/GenerateSlotsRequest"}
                            }
                        },
                    },
                    "responses": {
                        "201": _json_response(
                            "Created and already existing slots", "GeneratedSlots"
                        ),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks horario.generar"),
                        "404": _problem("Doctor or consultorio not found"),
                        "409": _problem("The doctor has not declared work hours"),
                        "422": _problem("Invalid range or dates"),
                    },
                },
            },
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
                        _query(
                            "especialidad_id",
                            "Optional: only slots of clinicians with this specialty",
                            required=False,
                            schema={"type": "integer", "minimum": 1},
                        ),
                        _query(
                            "servicio_id",
                            "Optional: only the slots of the service's window (samples in the "
                            "morning, consultations in the afternoon)",
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
                    "summary": "List own appointments or the doctor's agenda",
                    "description": (
                        "A patient sees own appointments; each row names its doctor in 'medico' "
                        "({id, nombre, registro_profesional, telefono}) so the patient can contact "
                        "them. A doctor with a doctor profile sees appointments of his or her "
                        "agenda; each row names the patient in 'paciente' ({id, nombre}; id is the "
                        "patient profile id used by lab orders). Any other caller gets 403, "
                        "administrators included: cancellation review uses "
                        "GET /api/v1/appointments/cancellation-approvals. Ordered by slot start, "
                        "then id."
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
                            "schema": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2147483647,
                                "default": 0,
                            },
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
                    "description": (
                        "A patient cancels an own appointment; a body is optional, may carry an "
                        "optional motivo_texto (up to 1500 characters, stored without a code) and "
                        "ignores motivo_codigo. A doctor cancels an appointment of an own agenda "
                        "and "
                        "must send motivo_codigo (IMPREVISTO_MEDICO or OTRO_MEDICO) and "
                        "motivo_texto (1 to 1500 characters). A paid appointment's refund is "
                        "requested for the patient after the cancellation."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "appointment_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CancelAppointmentRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": _json_response("Appointment cancelled", "Appointment"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Appointment not found or not owned by the caller (patient, or doctor "
                            "of the agenda)"
                        ),
                        "409": _problem(
                            "Appointment can no longer be cancelled, or a doctor's cancellation "
                            "needs an admin approval first: the 'detail' is "
                            "LIMITE_CANCELACION_MEDICO (monthly quota reached) and/or "
                            "CANCELACION_MEDICO_FUERA_DE_PLAZO (less than 24 hours of notice), "
                            "separated by commas"
                        ),
                        "422": _problem(
                            "Invalid body; a doctor without a valid reason (MOTIVO_CANCELACION_"
                            "REQUERIDO or MOTIVO_CANCELACION_INVALIDO)"
                        ),
                    },
                }
            },
            "/api/v1/appointments/{appointment_id}/consultation": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "openConsultation",
                    "summary": "Open the consultation of an appointment of the doctor's agenda",
                    "description": (
                        "Requires consultas.abrir (MEDICO) and an appointment of the caller's own "
                        "agenda. There is one consultation per appointment and the call is "
                        "idempotent: 201 when it is created, 200 with the existing one on a "
                        "repeat. The appointment must be CONFIRMADA or COMPLETADA (409 "
                        "otherwise). Missing and foreign appointments both answer 403."
                    ),
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
                        "201": _json_response("Consultation opened", "Consultation"),
                        "200": _json_response("Consultation already open", "Consultation"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Caller lacks consultas.abrir, or the appointment is missing or not "
                            "of the caller's agenda"
                        ),
                        "409": _problem("The appointment is not CONFIRMADA or COMPLETADA"),
                    },
                }
            },
            "/api/v1/appointments/{appointment_id}/consultation/close": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "closeConsultation",
                    "summary": "Register the attention and close the consultation",
                    "description": (
                        "Requires consultas.abrir (MEDICO) and an appointment of the caller's own "
                        "agenda in CONFIRMADA or COMPLETADA. observaciones (the diagnosis) is "
                        "required; plan_tratamiento is optional (up to 1000 characters each; "
                        "letters, digits and clinical punctuation only). Sets the consultation to "
                        "CERRADA with fin_atencion and the appointment to COMPLETADA. "
                        "Final: a repeat answers 409. Missing and foreign appointments answer 403."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "appointment_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "observaciones": {
                                            "type": ["string", "null"],
                                            "maxLength": 1000,
                                        },
                                        "plan_tratamiento": {
                                            "type": ["string", "null"],
                                            "maxLength": 1000,
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _json_response("Consultation closed", "Consultation"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Caller lacks consultas.abrir, or the appointment is missing or not "
                            "of the caller's agenda"
                        ),
                        "409": _problem(
                            "The appointment is not CONFIRMADA or COMPLETADA, or the consultation "
                            "is already registered"
                        ),
                        "422": _problem("A note is too long or contains characters not allowed"),
                    },
                }
            },
            "/api/v1/appointments/cancellation-approvals": {
                "get": {
                    "tags": ["Scheduling"],
                    "operationId": "listCancellationCandidates",
                    "summary": "List appointments for cancellation review (administrator)",
                    "description": (
                        "Requires citas.revisar_cancelaciones. Each row carries the doctor's "
                        "identity only: no patient and no doctor phone. Filter by the exact doctor "
                        "registration number with medico_registro (blank means no filter). Every "
                        "read and denial is audited."
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
                            "name": "medico_registro",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "maxLength": 80},
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
                            "schema": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 2147483647,
                                "default": 0,
                            },
                        },
                    ],
                    "responses": {
                        "200": _json_response(
                            "Appointments for cancellation review", "CancellationCandidateList"
                        ),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks citas.revisar_cancelaciones"),
                        "422": _problem("Invalid estado, medico_registro, limit or offset"),
                    },
                },
            },
            "/api/v1/appointments/{appointment_id}/cancellation-approval": {
                "post": {
                    "tags": ["Scheduling"],
                    "operationId": "approveDoctorCancellation",
                    "summary": "Approve one doctor cancellation beyond the quota or notice window",
                    "description": (
                        "Requires citas.revisar_cancelaciones (ADMINISTRADOR). "
                        "Records one approval for the appointment with a non-blank motivo "
                        "(up to 1500 characters). "
                        "The doctor then cancels it with the usual cancel endpoint. The approval "
                        "does not change the monthly quota count."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "appointment_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["motivo"],
                                    "properties": {"motivo": {"type": "string", "maxLength": 1500}},
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": _json_response("Approval recorded", "CancellationApproval"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks citas.revisar_cancelaciones"),
                        "404": _problem("Appointment not found"),
                        "409": _problem(
                            "Appointment can no longer be cancelled, or it already has an approval"
                        ),
                        "422": _problem("Missing or blank motivo (MOTIVO_APROBACION_REQUERIDO)"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "BookAppointmentRequest": BookAppointmentRequest.model_json_schema(),
                "WorkHoursRequest": WorkHoursRequest.model_json_schema(),
                "GenerateSlotsRequest": GenerateSlotsRequest.model_json_schema(),
                "WorkHoursList": {
                    "type": "object",
                    "required": ["vigente_desde", "actual", "proximo"],
                    "description": (
                        "actual: hours in force this week. proximo: hours from next week on, "
                        "the ones the doctor edits. Changes never reach the week in progress."
                    ),
                    "properties": {
                        "vigente_desde": {"type": "string", "format": "date"},
                        "actual": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/WorkBlock"},
                        },
                        "proximo": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/WorkBlock"},
                        },
                    },
                },
                "WorkBlock": {
                    "type": "object",
                    "required": ["dia_semana", "inicio", "fin"],
                    "properties": {
                        "dia_semana": {"type": "integer", "minimum": 0, "maximum": 6},
                        "inicio": {"type": "string", "example": "08:00"},
                        "fin": {"type": "string", "example": "10:00"},
                    },
                },
                "ConsultorioList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["id", "nombre", "sede"],
                                "properties": {
                                    "id": {"type": "string", "format": "uuid"},
                                    "nombre": {"type": "string"},
                                    "sede": {"type": "string"},
                                },
                            },
                        }
                    },
                },
                "ProposedSlotList": {
                    "type": "object",
                    "required": ["items", "total"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["inicio_utc", "fin_utc", "franja"],
                                "properties": {
                                    "inicio_utc": {"type": "string", "format": "date-time"},
                                    "fin_utc": {"type": "string", "format": "date-time"},
                                    "franja": {"type": "string", "enum": ["MANANA", "TARDE"]},
                                },
                            },
                        },
                        "total": {"type": "integer"},
                    },
                },
                "GeneratedSlots": {
                    "type": "object",
                    "required": ["creados", "existentes"],
                    "properties": {
                        "creados": {"type": "integer"},
                        "existentes": {"type": "integer"},
                    },
                },
                "CancelAppointmentRequest": CancelAppointmentRequest.model_json_schema(),
                "CancellationApproval": {
                    "type": "object",
                    "required": ["cita_id", "aprobada"],
                    "properties": {
                        "cita_id": {"type": "string", "format": "uuid"},
                        "aprobada": {"type": "boolean"},
                    },
                },
                "Consultation": {
                    "type": "object",
                    "required": ["id", "cita_id", "estado", "inicio_atencion", "motivo_consulta"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "cita_id": {"type": "string", "format": "uuid"},
                        "estado": {"type": "string", "enum": ["ABIERTA", "CERRADA"]},
                        "inicio_atencion": {"type": ["string", "null"], "format": "date-time"},
                        "motivo_consulta": {"type": ["string", "null"]},
                    },
                },
                "AppointmentSummary": {
                    "type": "object",
                    "required": ["id", "estado", "inicio", "fin"],
                    "properties": {
                        "paciente": {
                            "type": "object",
                            "description": "Only on a doctor's agenda rows (additive, optional)",
                            "required": ["id", "nombre"],
                            "properties": {
                                "id": {"type": "string", "format": "uuid"},
                                "nombre": {"type": "string"},
                            },
                        },
                        "medico": {
                            "type": "object",
                            "description": (
                                "The row's doctor, on a patient's own rows only (additive, "
                                "optional). telefono is present when the doctor has one."
                            ),
                            "required": ["id", "nombre", "registro_profesional"],
                            "properties": {
                                "id": {"type": "string", "format": "uuid"},
                                "nombre": {"type": "string"},
                                "registro_profesional": {"type": "string"},
                                "telefono": {"type": "string"},
                            },
                        },
                        "tiene_aprobacion": {
                            "type": "boolean",
                            "description": "True when cancellation approval is already registered",
                        },
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
                "CancellationCandidate": {
                    "type": "object",
                    "description": "Doctor identity only; no patient and no doctor phone.",
                    "required": ["id", "estado", "inicio", "fin", "tiene_aprobacion", "medico"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "estado": {"type": "string", "enum": sorted(APPOINTMENT_STATES)},
                        "inicio": {"type": "string", "format": "date-time"},
                        "fin": {"type": "string", "format": "date-time"},
                        "tiene_aprobacion": {
                            "type": "boolean",
                            "description": "True when cancellation approval is already registered",
                        },
                        "medico": {
                            "type": "object",
                            "required": ["id", "nombre", "registro_profesional"],
                            "properties": {
                                "id": {"type": "string", "format": "uuid"},
                                "nombre": {"type": "string"},
                                "registro_profesional": {"type": "string"},
                            },
                        },
                    },
                },
                "CancellationCandidateList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CancellationCandidate"},
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
                        "medico": {
                            "type": "object",
                            "description": "Public name of the clinician (additive, optional)",
                            "required": ["id", "nombre"],
                            "properties": {
                                "id": {"type": "string", "format": "uuid"},
                                "nombre": {"type": "string"},
                            },
                        },
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
                        "motivo": {"type": ["string", "null"], "maxLength": 1500},
                        "expiracion_reserva": {"type": ["string", "null"], "format": "date-time"},
                    },
                },
            }
        },
    }

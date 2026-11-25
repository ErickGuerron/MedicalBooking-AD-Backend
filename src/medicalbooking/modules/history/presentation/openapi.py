"""OpenAPI fragment for the clinical history. Contract first: routes must match it."""

from __future__ import annotations

from typing import Any

BEARER = [{"bearerAuth": []}]
_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}


def json_response(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def problem_response(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


_KINDS = ("alergias", "antecedentes", "medicacion", "problemas", "vacunas")
_PATIENT = {
    "name": "paciente_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}
_KIND = {
    "name": "path_kind",
    "in": "path",
    "required": True,
    "schema": {"type": "string", "enum": list(_KINDS)},
}
_ENTRY = {
    "name": "entry_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}


def _body(schema: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def history_fragment() -> dict[str, Any]:
    """Clinical history: verified entries, vital signs and their corrections."""
    responses_403 = problem_response(
        "Caller is not the treating doctor or the patient; the same answer for a missing resource"
    )
    return {
        "paths": {
            "/api/v1/patients/{paciente_id}/historia": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "readClinicalHistory",
                    "summary": "Read a patient's clinical history; active allergies first",
                    "security": BEARER,
                    "parameters": [_PATIENT],
                    "responses": {
                        "200": json_response("The history grouped by kind", "ClinicalHistory"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                    },
                },
            },
            "/api/v1/patients/{paciente_id}/historia/{path_kind}": {
                "post": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "createHistoryEntry",
                    "summary": "Register an entry. A doctor's entry is verified; a patient's is not",  # noqa: E501
                    "security": BEARER,
                    "parameters": [_PATIENT, _KIND],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"$ref": f"#/components/schemas/{name}"}
                                        for name in (
                                            "AllergyInput",
                                            "AntecedentInput",
                                            "MedicationInput",
                                            "ProblemInput",
                                            "VaccineInput",
                                        )
                                    ]
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": json_response("The created entry", "HistoryEntry"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                        "422": problem_response("A field is missing, unknown or out of its rule"),
                    },
                },
            },
            "/api/v1/patients/{paciente_id}/historia/{path_kind}/{entry_id}/verificar": {
                "post": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "verifyHistoryEntry",
                    "summary": "The treating doctor verifies a declared entry, once",
                    "security": BEARER,
                    "parameters": [_PATIENT, _KIND, _ENTRY],
                    "responses": {
                        "200": json_response("The verified entry", "HistoryEntry"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                        "409": problem_response(
                            "Already verified, or the kind does not need verifying"
                        ),
                    },
                },
            },
            "/api/v1/patients/{paciente_id}/historia/{path_kind}/{entry_id}/estado": {
                "patch": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "changeHistoryEntryState",
                    "summary": "Change an entry's state. Needs a motivo and the state read",
                    "security": BEARER,
                    "parameters": [_PATIENT, _KIND, _ENTRY],
                    "requestBody": _body("StateChangeInput"),
                    "responses": {
                        "200": json_response("The changed entry", "HistoryEntry"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                        "409": problem_response(
                            "The entry changed since it was read, or the transition is not allowed"
                        ),
                        "422": problem_response("Missing motivo or unknown state"),
                    },
                },
            },
            "/api/v1/citas/{cita_id}/signos-vitales": {
                "post": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "recordVitalSigns",
                    "summary": "Record vital signs for one of the caller's appointments",
                    "security": BEARER,
                    "parameters": [
                        {
                            "name": "cita_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": _body("VitalSignsInput"),
                    "responses": {
                        "201": json_response("The recorded vital signs", "VitalSigns"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                        "422": problem_response("No measure, unknown measure, or out of range"),
                    },
                },
            },
            "/api/v1/signos-vitales/{vitals_id}/correccion": {
                "post": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "correctVitalSigns",
                    "summary": "Correct vital signs: a new entry with a motivo; the original stays",
                    "security": BEARER,
                    "parameters": [
                        {
                            "name": "vitals_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "requestBody": _body("VitalSignsCorrectionInput"),
                    "responses": {
                        "201": json_response("The correction", "VitalSigns"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": responses_403,
                        "422": problem_response("Missing motivo, no measure, or out of range"),
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "AllergyInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sustancia", "categoria", "severidad"],
                    "properties": {
                        "sustancia": {"type": "string", "maxLength": 150},
                        "categoria": {
                            "type": "string",
                            "enum": ["MEDICAMENTO", "ALIMENTO", "LATEX", "AMBIENTAL", "OTRA"],
                        },
                        "reaccion": {"type": ["string", "null"], "maxLength": 300},
                        "severidad": {
                            "type": "string",
                            "enum": ["LEVE", "MODERADA", "GRAVE", "DESCONOCIDA"],
                        },
                    },
                },
                "AntecedentInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tipo", "descripcion"],
                    "properties": {
                        "tipo": {"type": "string", "enum": ["PERSONAL", "FAMILIAR", "QUIRURGICO"]},
                        "descripcion": {"type": "string", "maxLength": 500},
                        "parentesco": {
                            "type": ["string", "null"],
                            "maxLength": 50,
                            "description": "Required when tipo is FAMILIAR",
                        },
                        "cie11": {"type": ["string", "null"], "maxLength": 20, "example": "BA00"},
                    },
                },
                "MedicationInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["medicamento"],
                    "properties": {
                        "medicamento": {"type": "string", "maxLength": 150},
                        "dosis": {"type": ["string", "null"], "maxLength": 80},
                        "frecuencia": {"type": ["string", "null"], "maxLength": 80},
                        "inicio": {"type": ["string", "null"], "format": "date"},
                    },
                },
                "ProblemInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["descripcion"],
                    "description": "Only a treating doctor registers a problem.",
                    "properties": {
                        "descripcion": {"type": "string", "maxLength": 300},
                        "cie11": {"type": ["string", "null"], "maxLength": 20},
                        "inicio": {"type": ["string", "null"], "format": "date"},
                    },
                },
                "VaccineInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["vacuna", "dosis_numero", "fecha"],
                    "properties": {
                        "vacuna": {"type": "string", "maxLength": 120},
                        "dosis_numero": {"type": "integer", "minimum": 1, "maximum": 10},
                        "fecha": {
                            "type": "string",
                            "format": "date",
                            "description": "Not in the future",
                        },
                        "lote": {"type": ["string", "null"], "maxLength": 40},
                    },
                },
                "StateChangeInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["expected_estado", "estado", "motivo"],
                    "properties": {
                        "expected_estado": {
                            "type": "string",
                            "description": "The state the caller read",
                        },
                        "estado": {"type": "string", "description": "The target state"},
                        "motivo": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
                "VitalSignsInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "At least one measure; out-of-range values are refused.",
                    "properties": {
                        "peso_kg": {"type": "number", "minimum": 0.5, "maximum": 300},
                        "talla_cm": {"type": "number", "minimum": 30, "maximum": 250},
                        "pa_sistolica": {"type": "integer", "minimum": 50, "maximum": 260},
                        "pa_diastolica": {"type": "integer", "minimum": 30, "maximum": 160},
                        "frecuencia_cardiaca": {"type": "integer", "minimum": 20, "maximum": 250},
                        "temperatura_c": {"type": "number", "minimum": 30, "maximum": 45},
                        "saturacion": {"type": "integer", "minimum": 50, "maximum": 100},
                    },
                },
                "VitalSignsCorrectionInput": {
                    "allOf": [{"$ref": "#/components/schemas/VitalSignsInput"}],
                    "required": ["motivo"],
                    "properties": {"motivo": {"type": "string", "minLength": 1, "maxLength": 500}},
                },
                "HistoryEntry": {
                    "type": "object",
                    "description": "One entry. The value fields depend on the kind (tipo).",
                    "required": ["id", "tipo", "paciente_id", "origen", "verificada"],
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "tipo": {
                            "type": "string",
                            "enum": ["alergia", "antecedente", "medicacion", "problema", "vacuna"],
                        },
                        "paciente_id": {"type": "string", "format": "uuid"},
                        "estado": {"type": ["string", "null"]},
                        "origen": {
                            "type": "string",
                            "enum": ["REGISTRADA_MEDICO", "DECLARADA_PACIENTE"],
                        },
                        "verificada": {"type": "boolean"},
                        "verificada_por": {"type": ["string", "null"], "format": "uuid"},
                        "verificada_en": {"type": ["string", "null"], "format": "date-time"},
                        "registrada_en": {"type": ["string", "null"], "format": "date-time"},
                    },
                    "additionalProperties": True,
                },
                "VitalSigns": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "paciente_id": {"type": "string", "format": "uuid"},
                        "cita_id": {"type": "string", "format": "uuid"},
                        "corrige_a": {"type": ["string", "null"], "format": "uuid"},
                        "motivo_correccion": {"type": ["string", "null"]},
                        "registrada_en": {"type": ["string", "null"], "format": "date-time"},
                    },
                    "additionalProperties": {"type": ["number", "null"]},
                },
                "ClinicalHistory": {
                    "type": "object",
                    "required": [
                        "paciente_id",
                        "alergias",
                        "antecedentes",
                        "medicacion",
                        "problemas",
                        "vacunas",
                        "signos_vitales",
                    ],
                    "properties": {
                        "paciente_id": {"type": "string", "format": "uuid"},
                        "alergias": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoryEntry"},
                        },
                        "antecedentes": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoryEntry"},
                        },
                        "medicacion": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoryEntry"},
                        },
                        "problemas": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoryEntry"},
                        },
                        "vacunas": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HistoryEntry"},
                        },
                        "signos_vitales": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/VitalSigns"},
                        },
                    },
                },
            }
        },
    }

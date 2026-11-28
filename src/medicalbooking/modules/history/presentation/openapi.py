# ruff: noqa: E501  (OpenAPI summaries and descriptions are prose shown to readers, not code)
"""OpenAPI fragment for the clinical history. Contract first: the routes must match this document."""

from __future__ import annotations

from typing import Any

BEARER = [{"bearerAuth": []}]
_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_KINDS_ENUM = ["alergia", "antecedente", "medicacion", "problema", "vacuna", "signos_vitales"]
_ACTIONS_ENUM = ["CREAR", "VERIFICAR", "ESTADO", "CORREGIR"]


def json_response(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def problem_response(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM_CONTENT}


def _path_param(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "schema": schema}


def _query(name: str, schema: dict[str, Any], description: str = "") -> dict[str, Any]:
    param: dict[str, Any] = {"name": name, "in": "query", "required": False, "schema": schema}
    if description:
        param["description"] = description
    return param


_UUID = {"type": "string", "format": "uuid"}
_PATIENT = _path_param("paciente_id", _UUID)
_KIND = _path_param(
    "path_kind",
    {"type": "string", "enum": ["alergias", "antecedentes", "medicacion", "problemas", "vacunas"]},
)
_ENTRY = _path_param("entry_id", _UUID)
_DESDE = _query("desde", {"type": "string", "format": "date"}, "First day included (YYYY-MM-DD)")
_HASTA = _query(
    "hasta",
    {"type": "string", "format": "date"},
    "Last day included (YYYY-MM-DD). With desde, at most 366 days apart",
)
_LIMIT = _query("limit", {"type": "integer", "minimum": 1, "maximum": 200}, "Default 50")
_OFFSET = _query("offset", {"type": "integer", "minimum": 0})
_ENTIDAD = _query("entidad", {"type": "string", "enum": _KINDS_ENUM})
_ACCION = _query("accion", {"type": "string", "enum": _ACTIONS_ENUM})
_RESPONSES_403_PATIENT = problem_response(
    "Caller is not the treating doctor or the patient; the same answer for a missing resource"
)


def _body(schema: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def history_fragment() -> dict[str, Any]:
    """Clinical history: verified entries, catalogs, date ranges, the event log and vital signs."""
    return {
        "paths": {
            "/api/v1/patients/{paciente_id}/historia": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "readClinicalHistory",
                    "summary": "Read a patient's clinical history; active allergies first",
                    "security": BEARER,
                    "parameters": [_PATIENT, _DESDE, _HASTA],
                    "responses": {
                        "200": json_response("The history grouped by kind", "ClinicalHistory"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": _RESPONSES_403_PATIENT,
                        "422": problem_response("A date is malformed or the range is too wide"),
                    },
                },
            },
            "/api/v1/patients/{paciente_id}/historia/eventos": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "readHistoryEvents",
                    "summary": "Event log of a history, newest first. The doctor sees the actor; the patient does not",
                    "security": BEARER,
                    "parameters": [_PATIENT, _ENTIDAD, _ACCION, _DESDE, _HASTA, _LIMIT, _OFFSET],
                    "responses": {
                        "200": json_response("One page of the event log", "EventPage"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": _RESPONSES_403_PATIENT,
                        "422": problem_response("A filter, a range or a page is out of its rule"),
                    },
                },
            },
            "/api/v1/me/historia": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "readOwnClinicalHistory",
                    "summary": "The signed-in patient's own history",
                    "security": BEARER,
                    "parameters": [_DESDE, _HASTA],
                    "responses": {
                        "200": json_response("The history grouped by kind", "ClinicalHistory"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": problem_response("The account is not a patient's"),
                    },
                },
            },
            "/api/v1/me/historia/eventos": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "readOwnHistoryEvents",
                    "summary": "The signed-in patient's own event log, without the professional's identity",
                    "security": BEARER,
                    "parameters": [_ENTIDAD, _ACCION, _DESDE, _HASTA, _LIMIT, _OFFSET],
                    "responses": {
                        "200": json_response("One page of the event log", "EventPage"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": problem_response("The account is not a patient's"),
                    },
                },
            },
            "/api/v1/catalogos/{catalog_path}": {
                "get": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "searchCatalog",
                    "summary": "Active items of a catalog, for a picker. Any signed-in person may read them",
                    "security": BEARER,
                    "parameters": [
                        _path_param(
                            "catalog_path",
                            {"type": "string", "enum": ["cie11", "vacunas", "sustancias-alergia"]},
                        ),
                        _query("q", {"type": "string", "maxLength": 100}),
                        _LIMIT,
                    ],
                    "responses": {
                        "200": json_response("Matching active items", "CatalogList"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "422": problem_response("Unknown catalog, query or limit"),
                    },
                },
            },
            "/api/v1/patients/{paciente_id}/historia/{path_kind}": {
                "post": {
                    "tags": ["ClinicalHistory"],
                    "operationId": "createHistoryEntry",
                    "summary": "Register an entry. A doctor's entry is verified; a patient's is not",
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
                        "403": _RESPONSES_403_PATIENT,
                        "422": problem_response(
                            "A field is missing, unknown, outside the catalog or its rule"
                        ),
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
                        "403": _RESPONSES_403_PATIENT,
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
                        "403": _RESPONSES_403_PATIENT,
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
                    "parameters": [_path_param("cita_id", _UUID)],
                    "requestBody": _body("VitalSignsInput"),
                    "responses": {
                        "201": json_response("The recorded vital signs", "VitalSigns"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": _RESPONSES_403_PATIENT,
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
                    "parameters": [_path_param("vitals_id", _UUID)],
                    "requestBody": _body("VitalSignsCorrectionInput"),
                    "responses": {
                        "201": json_response("The correction", "VitalSigns"),
                        "401": problem_response("Missing, malformed or inactive credentials"),
                        "403": _RESPONSES_403_PATIENT,
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
                    "required": ["sustancia_codigo", "severidad"],
                    "description": "The allergen is a code of the allergen catalog; name and category come from it.",
                    "properties": {
                        "sustancia_codigo": {"type": "string", "example": "PENICILINA"},
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
                        "cie11": {
                            "type": ["string", "null"],
                            "maxLength": 20,
                            "example": "BA00",
                            "description": "Must be an active code of the CIE-11 catalog",
                        },
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
                        "cie11": {
                            "type": ["string", "null"],
                            "maxLength": 20,
                            "description": "Must be an active code of the CIE-11 catalog",
                        },
                        "inicio": {"type": ["string", "null"], "format": "date"},
                    },
                },
                "VaccineInput": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["vacuna_codigo", "dosis_numero", "fecha"],
                    "properties": {
                        "vacuna_codigo": {"type": "string", "example": "INFLUENZA"},
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
                "EventItem": {
                    "type": "object",
                    "required": ["id", "entidad", "entidad_id", "accion", "ocurrido_en"],
                    "properties": {
                        "id": {"type": "integer", "format": "int64"},
                        "entidad": {"type": "string", "enum": _KINDS_ENUM},
                        "entidad_id": {"type": "string", "format": "uuid"},
                        "accion": {"type": "string", "enum": _ACTIONS_ENUM},
                        "motivo": {"type": ["string", "null"]},
                        "antes": {"type": ["object", "null"], "additionalProperties": True},
                        "despues": {"type": ["object", "null"], "additionalProperties": True},
                        "ocurrido_en": {"type": "string", "format": "date-time"},
                        "actor_usuario_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "Only present when incluye_actor is true",
                        },
                    },
                },
                "EventPage": {
                    "type": "object",
                    "required": ["items", "total", "limit", "offset", "incluye_actor"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/EventItem"},
                        },
                        "total": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                        "incluye_actor": {"type": "boolean"},
                    },
                },
                "CatalogItem": {
                    "type": "object",
                    "required": ["codigo", "nombre"],
                    "properties": {
                        "codigo": {"type": "string"},
                        "nombre": {"type": "string"},
                        "categoria": {
                            "type": "string",
                            "description": "Only for the allergen catalog",
                        },
                    },
                },
                "CatalogList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CatalogItem"},
                        },
                    },
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

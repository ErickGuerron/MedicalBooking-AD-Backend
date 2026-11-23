"""OpenAPI fragment for laboratory: orders, manual results, medical review and PDF documents.

Request schemas are generated from the same Pydantic models that validate requests.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.laboratory.presentation.routes import (
    CreateOrderRequest,
    RecordResultsRequest,
    ReviewRequest,
)

_PROBLEM_CONTENT = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
}
_BEARER = [{"bearerAuth": []}]
_UUID_PARAM = {"type": "string", "format": "uuid"}


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


def laboratory_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/lab/orders": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabOrders",
                    "summary": "List orders the caller may read (own patient or ordering doctor)",
                    "description": (
                        "Newest first, then id. Rows carry no result values. A patient sees "
                        "own orders; a doctor sees orders placed by them; others get 403. A caller "
                        "with the doctor permission but no doctor profile gets 403 with the "
                        "doctor-profile message."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "estado",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "string",
                                "enum": ["SOLICITADA", "EN_PROCESO", "COMPLETADA", "CANCELADA"],
                            },
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
                        "200": _json("Orders", "LabOrderSummaryList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Caller has no order read access, or has the doctor permission but no "
                            "doctor profile ('A doctor profile is required for this action', "
                            "code DOCTOR_PROFILE_REQUIRED)"
                        ),
                        "422": _problem("Invalid estado, limit or offset"),
                    },
                },
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "createLabOrder",
                    "summary": "Create a laboratory order for a patient",
                    "description": (
                        "Requires ordenes.crear and a doctor profile. The patient must have a "
                        "cita or a lab order with the caller. Starts SOLICITADA. A patient the "
                        "caller does not treat (or an unknown id) gets the same 403 with code "
                        "PATIENT_NOT_AVAILABLE."
                    ),
                    "security": _BEARER,
                    "requestBody": _body("CreateOrderRequest"),
                    "responses": {
                        "201": _json("Order created", "LabOrder"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Forbidden. Each refusal carries a stable 'code' in the problem body. "
                            "'The patient is not available to this doctor' (code "
                            "PATIENT_NOT_AVAILABLE; the patient is unknown or not treated by the "
                            "caller, and both cases look the same); 'The patient account is not "
                            "active' (code PATIENT_ACCOUNT_INACTIVE; a patient the caller treats "
                            "whose account is not ACTIVO); 'A doctor profile is required for this "
                            "action' (code DOCTOR_PROFILE_REQUIRED; ordenes.crear but no medicos "
                            "profile); 'Access to this resource is not allowed' (no ordenes.crear; "
                            "no code)"
                        ),
                        "422": _problem("Invalid body or unknown test type"),
                    },
                },
            },
            "/api/v1/lab/orders/{order_id}": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "getLabOrder",
                    "summary": "Read an order (own patient or ordering doctor)",
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "responses": {
                        "200": _json("Order", "LabOrder"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Order not found or not accessible to the caller"),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/report.pdf": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "getLabResultsReport",
                    "summary": "Download the results of an order as a PDF",
                    "description": (
                        "Readable by whoever may read the order's results: the patient, the "
                        "ordering doctor and the laboratory. Served as an attachment."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "responses": {
                        "200": {
                            "description": "Results report",
                            "content": {
                                "application/pdf": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("The caller may not read this order"),
                        "404": _problem("Order not found"),
                    },
                },
            },
            "/api/v1/lab/orders/{order_id}/results": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "recordLabResults",
                    "summary": "Enter results by hand for one test of the order",
                    "description": (
                        "Requires resultados.registrar. Results carry no clinical "
                        "classification in this phase."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "requestBody": _body("RecordResultsRequest"),
                    "responses": {
                        "200": _json("Order after recording", "LabOrder"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                        "404": _problem("Order or test detail not found"),
                        "409": _problem("This test already has results"),
                        "422": _problem("Invalid result values"),
                    },
                },
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabResults",
                    "summary": "Read the results of an order (own patient or ordering doctor)",
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "responses": {
                        "200": _json("Results", "LabResultList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Order not found or not accessible to the caller"),
                    },
                },
            },
            "/api/v1/lab/orders/{order_id}/results/review": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "reviewLabResults",
                    "summary": "Read each result before confirming: confirm or correct it",
                    "description": (
                        "Requires resultados.registrar. Every reading is kept; the latest one is "
                        "effective. A reading that differs from the system's needs a motivo."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "requestBody": _body("ReviewResultsRequest"),
                    "responses": {
                        "200": {"description": "Results reviewed"},
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                        "404": _problem("Order or result not found"),
                        "422": _problem("Invalid reading, or a corrected reading without motivo"),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/review": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "reviewLabOrder",
                    "summary": "Register the medical review once, after all results exist",
                    "description": "Only the ordering doctor. Results are never rewritten.",
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "requestBody": _body("ReviewRequest"),
                    "responses": {
                        "201": _json("Review registered", "ReviewResponse"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not the ordering doctor"),
                        "409": _problem("Results incomplete or already reviewed"),
                        "422": _problem("Invalid conclusion"),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/confirm": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "confirmLabOrder",
                    "summary": "Close a completed order, with or without its report",
                    "description": (
                        "Requires resultados.registrar. Every test must have its results. The "
                        "order is closed: results and reports can no longer change, and the "
                        "results email (with the report, if any) goes out now."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "responses": {
                        "200": {"description": "Order confirmed"},
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                        "404": _problem("Order not found"),
                        "409": _problem(
                            "Not completed, already confirmed, or a result still awaits review"
                        ),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/documents": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "uploadLabDocument",
                    "summary": "Upload the PDF report of an order (private storage)",
                    "description": (
                        "multipart/form-data with field 'archivo' (application/pdf, at most "
                        "10 MB, %PDF- signature). Stored privately; never returned as a URL."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "required": ["archivo"],
                                    "properties": {
                                        "archivo": {"type": "string", "format": "binary"},
                                        "tipo_documento": {
                                            "type": "string",
                                            "enum": ["INFORME_LABORATORIO"],
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": _json("Document stored", "LabDocument"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                        "404": _problem("Order not found"),
                        "422": _problem("Not a PDF, empty, or larger than 10 MB"),
                    },
                },
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabOrderDocuments",
                    "summary": "List the uploaded reports of an order",
                    "description": (
                        "Requires resultados.registrar, or the same read access as the order "
                        "(patient, ordering doctor). Metadata only: download each one with "
                        "GET /documents/{id}/download. Other callers get the uniform 403."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("order_id")],
                    "responses": {
                        "200": _json("Reports of the order", "LabDocumentList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Access to this resource is not allowed"),
                    },
                },
            },
            "/api/v1/patients": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listPatients",
                    "summary": "List patient profiles for the lab order form",
                    "description": (
                        "Requires ordenes.crear and a doctor profile. Lists only the patients "
                        "the caller treats (a cita or a lab order, any status) with an ACTIVO "
                        "account. Returns the profile id (pacientes.id) and the names only. "
                        "Ordered by apellido, nombre, id. Each call is audited without values."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
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
                        "200": _json("Patient page", "PatientList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem(
                            "Forbidden. Two messages in the detail: "
                            "'Access to this resource is not allowed' (caller lacks "
                            "ordenes.crear; no code); 'A doctor profile is required for this "
                            "action' (ordenes.crear but no medicos profile; code "
                            "DOCTOR_PROFILE_REQUIRED)"
                        ),
                        "422": _problem("limit outside 1 to 100, or an invalid offset"),
                    },
                }
            },
            "/api/v1/lab/patient-orders": {
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "createPatientLabOrder",
                    "summary": "Request a lab exam as the patient, with no doctor",
                    "description": (
                        "Requires ordenes.solicitar_propias and a patient profile. Creates an "
                        "order with origen PACIENTE, medico_id null and prioridad NORMAL. It "
                        "skips the doctor, so it has no review and no proposal. Results follow "
                        "the same flow."
                    ),
                    "security": _BEARER,
                    "requestBody": _body("PatientOrderRequest"),
                    "responses": {
                        "201": _json("Order created", "LabWorkItem"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller has no patient profile or no permission"),
                        "422": _problem("Invalid body or unknown or inactive test type"),
                    },
                }
            },
            "/api/v1/lab/worklist": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabWorklist",
                    "summary": "List the pending orders the laboratory works on, both origins",
                    "description": (
                        "Requires resultados.registrar. Orders in SOLICITADA or EN_PROCESO: "
                        "urgent first, then oldest, then id. Each test line carries its nombre and "
                        "codigo to record results (detalle_id). "
                        "Audited without values."
                    ),
                    "security": _BEARER,
                    "parameters": [
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
                        "200": _json("Pending orders", "LabWorklist"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                        "422": _problem("Invalid limit or offset"),
                    },
                }
            },
            "/api/v1/lab/test-types/{test_id}/analytes": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabTestAnalytes",
                    "summary": "List the analytes a test asks for, in presentation order",
                    "description": (
                        "Requires resultados.registrar. Active analytes only. obligatorio marks "
                        "the ones a test needs to be complete; orden is the presentation order "
                        "(null when unordered)."
                    ),
                    "security": _BEARER,
                    "parameters": [_path_param("test_id")],
                    "responses": {
                        "200": _json("Analytes", "LabTestAnalyteList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller lacks resultados.registrar"),
                    },
                }
            },
            "/api/v1/lab/test-types": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "listLabTestTypes",
                    "summary": "List the active laboratory test types",
                    "description": (
                        "Any authenticated caller. Static synthetic catalog: only active rows, "
                        "ordered by nombre then id. Not audited (no personal data)."
                    ),
                    "security": _BEARER,
                    "responses": {
                        "200": _json("Active test types", "LabTestTypeList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                    },
                }
            },
            "/api/v1/documents/{document_id}/download": {
                "get": {
                    "tags": ["Laboratory"],
                    "operationId": "downloadLabDocument",
                    "summary": "Download a stored PDF (patient owner or ordering doctor)",
                    "description": "The response is an attachment with no-store caching.",
                    "security": _BEARER,
                    "parameters": [_path_param("document_id")],
                    "responses": {
                        "200": {
                            "description": "PDF content",
                            "content": {
                                "application/pdf": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Document not found or not accessible to the caller"),
                        "500": _problem("Stored file failed the integrity check"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "CreateOrderRequest": CreateOrderRequest.model_json_schema(),
                "RecordResultsRequest": RecordResultsRequest.model_json_schema(),
                "ReviewRequest": ReviewRequest.model_json_schema(),
                "PatientOrderRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tipo_prueba_ids"],
                    "properties": {
                        "tipo_prueba_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 20,
                            "items": _UUID_PARAM,
                        },
                        "observaciones": {"type": ["string", "null"], "maxLength": 1000},
                    },
                },
                "LabTestAnalyte": {
                    "type": "object",
                    "required": ["id", "nombre", "unidad", "tipo_valor", "obligatorio", "orden"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "nombre": {"type": "string"},
                        "unidad": {"type": ["string", "null"]},
                        "tipo_valor": {
                            "type": "string",
                            "enum": ["NUMERICO", "TEXTO", "CODIFICADO"],
                        },
                        "obligatorio": {"type": "boolean"},
                        "orden": {"type": ["integer", "null"]},
                    },
                },
                "LabTestAnalyteList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabTestAnalyte"},
                        }
                    },
                },
                "LabWorkItem": {
                    "allOf": [
                        {"$ref": "#/components/schemas/LabOrder"},
                        {
                            "type": "object",
                            "required": ["origen", "fecha", "paciente_nombre"],
                            "properties": {
                                "origen": {"type": "string", "enum": ["MEDICO", "PACIENTE"]},
                                "fecha": {"type": "string", "format": "date-time"},
                                "paciente_nombre": {"type": ["string", "null"]},
                            },
                        },
                    ]
                },
                "LabWorklist": {
                    "type": "object",
                    "required": ["items", "limit", "offset"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabWorkItem"},
                        },
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                },
                "LabOrder": {
                    "type": "object",
                    "required": ["id", "paciente_id", "prioridad", "estado", "pruebas"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "paciente_id": _UUID_PARAM,
                        "prioridad": {"type": "string", "enum": ["NORMAL", "URGENTE"]},
                        "estado": {
                            "type": "string",
                            "enum": ["SOLICITADA", "EN_PROCESO", "COMPLETADA", "CANCELADA"],
                        },
                        "observaciones": {"type": ["string", "null"]},
                        "medico_id": {"type": ["string", "null"], "format": "uuid"},
                        "pruebas": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "detalle_id": _UUID_PARAM,
                                    "tipo_prueba_id": _UUID_PARAM,
                                    "estado": {
                                        "type": "string",
                                        "enum": [
                                            "PENDIENTE",
                                            "EN_PROCESO",
                                            "COMPLETADO",
                                            "CANCELADO",
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
                "LabOrderSummary": {
                    "type": "object",
                    "required": ["id", "paciente_id", "medico_id", "estado", "fecha"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "paciente_id": _UUID_PARAM,
                        "medico_id": {"type": ["string", "null"], "format": "uuid"},
                        "estado": {
                            "type": "string",
                            "enum": ["SOLICITADA", "EN_PROCESO", "COMPLETADA", "CANCELADA"],
                        },
                        "fecha": {"type": "string", "format": "date-time"},
                    },
                },
                "LabOrderSummaryList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabOrderSummary"},
                        }
                    },
                },
                "LabResult": {
                    "type": "object",
                    "properties": {
                        "id": _UUID_PARAM,
                        "detalle_id": _UUID_PARAM,
                        "nombre_analito": {"type": "string"},
                        "valor_texto": {"type": ["string", "null"]},
                        "valor_numerico": {"type": ["number", "null"]},
                        "unidad": {"type": ["string", "null"]},
                        "referencia_min": {"type": ["number", "null"]},
                        "referencia_max": {"type": ["number", "null"]},
                        "interpretacion": {
                            "type": ["string", "null"],
                            "enum": ["BAJO", "DENTRO_REFERENCIA", "ALTO", "NO_EVALUABLE", None],
                        },
                        "interpretacion_sistema": {
                            "type": ["string", "null"],
                            "description": "Reading computed by the system; never changed",
                            "enum": ["BAJO", "DENTRO_REFERENCIA", "ALTO", "NO_EVALUABLE", None],
                        },
                        "revisado": {"type": "boolean"},
                        "corregido": {
                            "type": "boolean",
                            "description": "True when the effective reading differs from the "
                            "system's",
                        },
                        "motivo_correccion": {"type": ["string", "null"], "maxLength": 1000},
                    },
                },
                "ResultReviewItem": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["resultado_id", "interpretacion_final"],
                    "properties": {
                        "resultado_id": _UUID_PARAM,
                        "interpretacion_final": {
                            "type": "string",
                            "enum": ["BAJO", "DENTRO_REFERENCIA", "ALTO", "NO_EVALUABLE"],
                        },
                        "motivo": {"type": ["string", "null"], "maxLength": 1000},
                    },
                },
                "ReviewResultsRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["revisiones"],
                    "properties": {
                        "revisiones": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 200,
                            "items": {"$ref": "#/components/schemas/ResultReviewItem"},
                        }
                    },
                },
                "LabResultList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabResult"},
                        }
                    },
                },
                "PatientSummary": {
                    "type": "object",
                    "required": ["id", "nombres", "apellidos"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "nombres": {"type": "string"},
                        "apellidos": {"type": "string"},
                    },
                },
                "PatientList": {
                    "type": "object",
                    "required": ["items", "limit", "offset"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PatientSummary"},
                        },
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                },
                "LabTestType": {
                    "type": "object",
                    "required": ["id", "codigo", "nombre", "descripcion", "unidad_predeterminada"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "codigo": {"type": "string"},
                        "nombre": {"type": "string"},
                        "descripcion": {"type": ["string", "null"]},
                        "unidad_predeterminada": {"type": ["string", "null"]},
                    },
                },
                "LabTestTypeList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabTestType"},
                        }
                    },
                },
                "ReviewResponse": {
                    "type": "object",
                    "properties": {
                        "estado": {"type": "string", "enum": ["COMPLETADA"]},
                        "orden_id": _UUID_PARAM,
                    },
                },
                "LabDocumentList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/LabDocument"},
                        }
                    },
                },
                "LabDocument": {
                    "type": "object",
                    "properties": {
                        "id": _UUID_PARAM,
                        "orden_id": _UUID_PARAM,
                        "nombre_archivo": {"type": "string"},
                        "tipo_mime": {"type": "string", "enum": ["application/pdf"]},
                        "tamano_bytes": {"type": "integer"},
                        "hash_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                },
            }
        },
    }

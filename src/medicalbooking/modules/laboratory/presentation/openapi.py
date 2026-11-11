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
                        "own orders; a doctor sees orders placed by them; others get 403."
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
                            "schema": {"type": "integer", "minimum": 0, "default": 0},
                        },
                    ],
                    "responses": {
                        "200": _json("Orders", "LabOrderSummaryList"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller has no order read access"),
                        "422": _problem("Invalid estado, limit or offset"),
                    },
                },
                "post": {
                    "tags": ["Laboratory"],
                    "operationId": "createLabOrder",
                    "summary": "Create a laboratory order for a patient",
                    "description": (
                        "Requires ordenes.crear and a doctor profile. Starts SOLICITADA."
                    ),
                    "security": _BEARER,
                    "requestBody": _body("CreateOrderRequest"),
                    "responses": {
                        "201": _json("Order created", "LabOrder"),
                        "401": _problem("Missing, malformed or inactive credentials"),
                        "403": _problem("Caller is not an ordering doctor"),
                        "404": _problem("Patient not found"),
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
                    "required": ["id", "paciente_id", "estado", "fecha"],
                    "properties": {
                        "id": _UUID_PARAM,
                        "paciente_id": _UUID_PARAM,
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
                "ReviewResponse": {
                    "type": "object",
                    "properties": {
                        "estado": {"type": "string", "enum": ["COMPLETADA"]},
                        "orden_id": _UUID_PARAM,
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

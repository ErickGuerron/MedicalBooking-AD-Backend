"""OpenAPI fragment for clinical knowledge and proposals (E5).

Request schemas come from the same Pydantic models that validate requests.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.clinical.presentation.routes import (
    AnalyteRequest,
    ProposalReviewRequest,
    ReferenceRequest,
    RuleRequest,
)

_BEARER = [{"bearerAuth": []}]
_UUID = {"type": "string", "format": "uuid"}
_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM}


def _ok(description: str, schema: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def _body(schema: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def _path(name: str) -> dict[str, Any]:
    return {"name": name, "in": "path", "required": True, "schema": _UUID}


_AUTH_ERRORS = {
    "401": _problem("Missing, malformed or inactive credentials"),
    "403": _problem("Caller lacks the permission or is not the owner"),
}


def clinical_fragment() -> dict[str, Any]:
    return {
        "paths": {
            "/api/v1/knowledge/analytes": {
                "get": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "listKnowledgeAnalytes",
                    "summary": "List active analytes",
                    "security": _BEARER,
                    "responses": {"200": _ok("Analytes", "AnalyteList"), **_AUTH_ERRORS},
                },
                "post": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "createKnowledgeAnalyte",
                    "summary": "Create an analyte (administrator)",
                    "security": _BEARER,
                    "requestBody": _body("AnalyteRequest"),
                    "responses": {
                        "201": _ok("Created", "IdResponse"),
                        **_AUTH_ERRORS,
                        "422": _problem("Invalid body"),
                    },
                },
            },
            "/api/v1/knowledge/analytes/{analyte_id}/references": {
                "get": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "listValidatedReferences",
                    "summary": "Validated reference ranges valid on a date",
                    "security": _BEARER,
                    "parameters": [
                        _path("analyte_id"),
                        {
                            "name": "fecha",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "format": "date"},
                        },
                    ],
                    "responses": {
                        "200": _ok("References", "ReferenceList"),
                        **_AUTH_ERRORS,
                        "404": _problem("Analyte not found"),
                    },
                },
                "post": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "createReferenceDraft",
                    "summary": "Create a draft reference range (administrator)",
                    "description": "Drafts are invisible until a doctor validates them.",
                    "security": _BEARER,
                    "parameters": [_path("analyte_id")],
                    "requestBody": _body("ReferenceRequest"),
                    "responses": {
                        "201": _ok("Draft created", "IdResponse"),
                        **_AUTH_ERRORS,
                        "404": _problem("Analyte not found"),
                        "422": _problem("Invalid range, dates or source"),
                    },
                },
            },
            "/api/v1/knowledge/references/{reference_id}/validate": {
                "post": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "validateReference",
                    "summary": "Validate a draft reference (doctor)",
                    "description": "Conflicts with 409 when it overlaps a validated reference.",
                    "security": _BEARER,
                    "parameters": [_path("reference_id")],
                    "responses": {
                        "200": _ok("Validated", "StateResponse"),
                        **_AUTH_ERRORS,
                        "404": _problem("Reference not found"),
                        "409": _problem("Not a draft, or overlaps a validated reference"),
                    },
                }
            },
            "/api/v1/knowledge/rules": {
                "post": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "createRuleDraft",
                    "summary": "Create a declarative rule draft (administrator)",
                    "security": _BEARER,
                    "requestBody": _body("RuleRequest"),
                    "responses": {
                        "201": _ok("Draft created", "IdResponse"),
                        **_AUTH_ERRORS,
                        "422": _problem("Invalid rule or unknown analyte"),
                    },
                }
            },
            "/api/v1/knowledge/rules/{rule_id}/validate": {
                "post": {
                    "tags": ["Clinical knowledge"],
                    "operationId": "validateRule",
                    "summary": "Validate a rule draft (doctor)",
                    "security": _BEARER,
                    "parameters": [_path("rule_id")],
                    "responses": {
                        "200": _ok("Validated", "StateResponse"),
                        **_AUTH_ERRORS,
                        "404": _problem("Rule not found"),
                        "409": _problem("Not a draft"),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/evaluate": {
                "post": {
                    "tags": ["Clinical proposals"],
                    "operationId": "evaluateLabOrder",
                    "summary": "Create the preliminary proposal of a complete order",
                    "description": (
                        "Deterministic. Only validated rules run. The result is a preliminary "
                        "proposal, never a diagnosis. One proposal per order."
                    ),
                    "security": _BEARER,
                    "parameters": [_path("order_id")],
                    "responses": {
                        "201": _ok("Proposal", "Proposal"),
                        **_AUTH_ERRORS,
                        "409": _problem("Order incomplete, or already evaluated"),
                    },
                }
            },
            "/api/v1/lab/orders/{order_id}/proposal": {
                "get": {
                    "tags": ["Clinical proposals"],
                    "operationId": "getLabProposal",
                    "summary": "Read the proposal of an order (ordering doctor)",
                    "security": _BEARER,
                    "parameters": [_path("order_id")],
                    "responses": {
                        "200": _ok("Proposal", "Proposal"),
                        **_AUTH_ERRORS,
                        "404": _problem("No proposal yet"),
                    },
                }
            },
            "/api/v1/clinical/proposals/{proposal_id}/reviews": {
                "post": {
                    "tags": ["Clinical proposals"],
                    "operationId": "reviewClinicalProposal",
                    "summary": "Record the single medical decision on a proposal",
                    "description": (
                        "ACEPTAR, CORREGIR (needs conclusion_corregida) or RECHAZAR (needs "
                        "motivo). The original proposal is never rewritten."
                    ),
                    "security": _BEARER,
                    "parameters": [_path("proposal_id")],
                    "requestBody": _body("ProposalReviewRequest"),
                    "responses": {
                        "201": _ok("Decision recorded", "ProposalReviewResponse"),
                        **_AUTH_ERRORS,
                        "404": _problem("Proposal not found"),
                        "409": _problem("Already decided"),
                        "422": _problem("Missing text for the decision"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "AnalyteRequest": AnalyteRequest.model_json_schema(),
                "ReferenceRequest": ReferenceRequest.model_json_schema(),
                "RuleRequest": RuleRequest.model_json_schema(),
                "ProposalReviewRequest": ProposalReviewRequest.model_json_schema(),
                "IdResponse": {
                    "type": "object",
                    "properties": {"id": _UUID, "estado": {"type": "string"}},
                },
                "StateResponse": {
                    "type": "object",
                    "properties": {"id": _UUID, "estado": {"type": "string"}},
                },
                "ProposalReviewResponse": {
                    "type": "object",
                    "properties": {"id": _UUID, "decision": {"type": "string"}},
                },
                "AnalyteList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {"type": "object"}}},
                },
                "ReferenceList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {"type": "object"}}},
                },
                "Proposal": {
                    "type": "object",
                    "required": ["id", "orden_id", "estado", "hallazgos", "aviso"],
                    "properties": {
                        "id": _UUID,
                        "orden_id": _UUID,
                        "estado": {
                            "type": "string",
                            "enum": [
                                "PENDIENTE",
                                "ACEPTADA",
                                "CORREGIDA",
                                "RECHAZADA",
                                "SIN_EVIDENCIA",
                            ],
                        },
                        "version_motor": {"type": "string"},
                        "resumen": {"type": "string"},
                        "hallazgos": {"type": "array", "items": {"type": "object"}},
                        "decision": {"type": ["string", "null"]},
                        "conclusion_medica": {"type": ["string", "null"]},
                        "aviso": {"type": "string"},
                    },
                },
            }
        },
    }

"""OpenAPI fragment for the catalog: specialties, doctors and services.

Request schemas come from the same Pydantic models that validate the requests.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.catalog.presentation.routes import (
    CreateServiceRequest,
    CreateSpecialtyRequest,
    SetDoctorSpecialtiesRequest,
    SetServiceSpecialtyRequest,
    UpdateSpecialtyRequest,
)

_BEARER = [{"bearerAuth": []}]
_TAG = ["Catalog"]
_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}


def _ok(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": _PROBLEM}


def _body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
        },
    }


def _path(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "description": description,
        "schema": schema,
    }


_ADMIN_ERRORS = {
    "401": _problem("Missing, malformed or inactive credentials"),
    "403": _problem("Caller lacks catalogo.administrar"),
}


def _list_errors() -> dict[str, Any]:
    return {"401": _problem("Missing, malformed or inactive credentials")}


def catalog_fragment() -> dict[str, Any]:
    uuid_schema = {"type": "string", "format": "uuid"}
    return {
        "paths": {
            "/api/v1/specialties": {
                "get": {
                    "tags": _TAG,
                    "operationId": "listSpecialties",
                    "summary": "List the active medical specialties",
                    "description": "Any signed-in user. Ordered by name.",
                    "security": _BEARER,
                    "responses": {
                        "200": _ok("Active specialties", "SpecialtyList"),
                        **_list_errors(),
                    },
                },
                "post": {
                    "tags": _TAG,
                    "operationId": "createSpecialty",
                    "summary": "Create a specialty",
                    "description": "Requires catalogo.administrar (ADMINISTRADOR).",
                    "security": _BEARER,
                    "requestBody": _body("CreateSpecialtyRequest"),
                    "responses": {
                        "201": _ok("Specialty created", "Specialty"),
                        **_ADMIN_ERRORS,
                        "409": _problem("A specialty with this name already exists"),
                        "422": _problem("Invalid body"),
                    },
                },
            },
            "/api/v1/specialties/{especialidad_id}": {
                "patch": {
                    "tags": _TAG,
                    "operationId": "updateSpecialty",
                    "summary": "Rename, describe or deactivate a specialty",
                    "description": (
                        "Requires catalogo.administrar. Only the fields sent are changed; "
                        "'activo' false removes the specialty from the listings."
                    ),
                    "security": _BEARER,
                    "parameters": [_path("especialidad_id", "Specialty id", {"type": "integer"})],
                    "requestBody": _body("UpdateSpecialtyRequest"),
                    "responses": {
                        "200": _ok("Specialty updated", "Specialty"),
                        **_ADMIN_ERRORS,
                        "404": _problem("Specialty not found"),
                        "409": _problem("A specialty with this name already exists"),
                        "422": _problem("Invalid body, or no field sent"),
                    },
                }
            },
            "/api/v1/doctors": {
                "get": {
                    "tags": _TAG,
                    "operationId": "listDoctors",
                    "summary": "List the active doctors, optionally by specialty",
                    "description": (
                        "Any signed-in user. Public data only: no professional registry and no "
                        "e-mail. 'horario_atencion' is derived from existing data: 'vigente_desde' "
                        "and 'vigente_hasta' are the earliest start and latest end of the "
                        "doctor's active agendas (hasta is null when any agenda is open-ended); "
                        "'dias' lists, per ISO weekday (1 = Monday), the earliest start and "
                        "latest end, in the local time of the site, of the doctor's slots in the "
                        "next 31 days (not blocked). Gaps inside a day are not shown. A doctor "
                        "without an active agenda has null dates and no days."
                    ),
                    "security": _BEARER,
                    "parameters": [
                        {
                            "name": "especialidad_id",
                            "in": "query",
                            "required": False,
                            "description": "Only doctors with this (active) specialty",
                            "schema": {"type": "integer", "minimum": 1},
                        }
                    ],
                    "responses": {
                        "200": _ok("Active doctors", "DoctorList"),
                        **_list_errors(),
                        "422": _problem("Invalid especialidad_id"),
                    },
                }
            },
            "/api/v1/doctors/{medico_id}/specialties": {
                "put": {
                    "tags": _TAG,
                    "operationId": "setDoctorSpecialties",
                    "summary": "Replace the specialties of a doctor",
                    "description": (
                        "Requires catalogo.administrar. The list replaces the current set; an "
                        "empty list removes all of them. Every id must be an active specialty."
                    ),
                    "security": _BEARER,
                    "parameters": [_path("medico_id", "Doctor id", uuid_schema)],
                    "requestBody": _body("SetDoctorSpecialtiesRequest"),
                    "responses": {
                        "200": _ok("Specialties now assigned", "DoctorSpecialties"),
                        **_ADMIN_ERRORS,
                        "404": _problem("Doctor not found"),
                        "422": _problem("Invalid body, or an unknown or inactive specialty"),
                    },
                }
            },
            "/api/v1/services": {
                "get": {
                    "tags": _TAG,
                    "operationId": "listServices",
                    "summary": "List the active medical services",
                    "description": "Any signed-in user. 'precio_base' is a decimal string.",
                    "security": _BEARER,
                    "responses": {
                        "200": _ok("Active services", "ServiceList"),
                        **_list_errors(),
                    },
                },
                "post": {
                    "tags": _TAG,
                    "operationId": "createService",
                    "summary": "Create a medical service",
                    "description": "Requires catalogo.administrar. The service is created active.",
                    "security": _BEARER,
                    "requestBody": _body("CreateServiceRequest"),
                    "responses": {
                        "201": _ok("Service created", "CatalogService"),
                        **_ADMIN_ERRORS,
                        "422": _problem("Invalid body, or an unknown or inactive specialty"),
                    },
                },
            },
            "/api/v1/services/{servicio_id}": {
                "patch": {
                    "tags": _TAG,
                    "operationId": "setServiceSpecialty",
                    "summary": "Set (or clear) the specialty of a service",
                    "description": "Requires catalogo.administrar. Null clears the link.",
                    "security": _BEARER,
                    "parameters": [_path("servicio_id", "Service id", uuid_schema)],
                    "requestBody": _body("SetServiceSpecialtyRequest"),
                    "responses": {
                        "200": _ok("Service updated", "CatalogService"),
                        **_ADMIN_ERRORS,
                        "404": _problem("Service not found"),
                        "422": _problem("Invalid body, or an unknown or inactive specialty"),
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "CreateSpecialtyRequest": CreateSpecialtyRequest.model_json_schema(),
                "UpdateSpecialtyRequest": UpdateSpecialtyRequest.model_json_schema(),
                "SetDoctorSpecialtiesRequest": SetDoctorSpecialtiesRequest.model_json_schema(),
                "CreateServiceRequest": CreateServiceRequest.model_json_schema(),
                "SetServiceSpecialtyRequest": SetServiceSpecialtyRequest.model_json_schema(),
                "Specialty": {
                    "type": "object",
                    "required": ["id", "nombre", "descripcion"],
                    "properties": {
                        "id": {"type": "integer"},
                        "nombre": {"type": "string"},
                        "descripcion": {"type": ["string", "null"]},
                    },
                },
                "SpecialtyList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Specialty"},
                        }
                    },
                },
                "SpecialtyRef": {
                    "type": "object",
                    "required": ["id", "nombre"],
                    "properties": {"id": {"type": "integer"}, "nombre": {"type": "string"}},
                },
                "AttentionSchedule": {
                    "type": "object",
                    "required": ["vigente_desde", "vigente_hasta", "dias"],
                    "properties": {
                        "vigente_desde": {"type": ["string", "null"], "format": "date"},
                        "vigente_hasta": {"type": ["string", "null"], "format": "date"},
                        "dias": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["dia_semana", "desde", "hasta"],
                                "properties": {
                                    "dia_semana": {"type": "integer", "minimum": 1, "maximum": 7},
                                    "desde": {
                                        "type": "string",
                                        "pattern": "^[0-2][0-9]:[0-5][0-9]$",
                                    },
                                    "hasta": {
                                        "type": "string",
                                        "pattern": "^[0-2][0-9]:[0-5][0-9]$",
                                    },
                                },
                            },
                        },
                    },
                },
                "Doctor": {
                    "type": "object",
                    "required": [
                        "id",
                        "nombre",
                        "especialidades",
                        "duracion_cita_min",
                        "horario_atencion",
                    ],
                    "properties": {
                        "id": uuid_schema,
                        "nombre": {"type": "string"},
                        "especialidades": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SpecialtyRef"},
                        },
                        "duracion_cita_min": {"type": "integer"},
                        "horario_atencion": {"$ref": "#/components/schemas/AttentionSchedule"},
                    },
                },
                "DoctorList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/Doctor"}}
                    },
                },
                "DoctorSpecialties": {
                    "type": "object",
                    "required": ["medico_id", "especialidades"],
                    "properties": {
                        "medico_id": uuid_schema,
                        "especialidades": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SpecialtyRef"},
                        },
                    },
                },
                "CatalogService": {
                    "type": "object",
                    "required": [
                        "id",
                        "nombre",
                        "duracion_min",
                        "precio_base",
                        "moneda",
                        "especialidad_id",
                        "requiere_orden_medica",
                    ],
                    "properties": {
                        "id": uuid_schema,
                        "nombre": {"type": "string"},
                        "duracion_min": {"type": "integer"},
                        "precio_base": {"type": "string"},
                        "moneda": {"type": "string"},
                        "especialidad_id": {"type": ["integer", "null"]},
                        "requiere_orden_medica": {"type": "boolean"},
                    },
                },
                "ServiceList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CatalogService"},
                        }
                    },
                },
            }
        },
    }

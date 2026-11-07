"""HTTP adapter for identity: /api/v1/auth, /api/v1/me, /api/v1/patients, /api/v1/users."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.shared.domain.errors import Unauthenticated, ValidationFailed

identity_bp = Blueprint("identity", __name__, url_prefix="/api/v1")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    roles: list[str] = Field(min_length=1, max_length=4)


def _service() -> IdentityService:
    return current_app.extensions["medicalbooking.identity"]


def _parse(model: type[BaseModel]) -> Any:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationFailed("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as error:
        # Report field names only: never echo values, which may be passwords.
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise ValidationFailed("Invalid fields: " + ", ".join(fields)) from None


def authenticated(view: Callable[..., Response]) -> Callable[..., Response]:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Response:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not token:
            raise Unauthenticated("Authentication required")
        g.actor = _service().authenticate_token(token)
        return view(*args, **kwargs)

    return wrapper


@identity_bp.post("/auth/login")
def login() -> tuple[Response, int]:
    body = _parse(LoginRequest)
    issued = _service().login(body.email, body.password)
    return (
        jsonify(
            access_token=issued.access_token,
            token_type="Bearer",  # noqa: S106 - OAuth token type name, not a password
            expires_in=issued.expires_in,
        ),
        200,
    )


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)


@identity_bp.post("/auth/register")
def register() -> tuple[Response, int]:
    """Public patient self-registration. Always answers the same, whatever the email."""
    body = _parse(RegisterRequest)
    message = _service().register_patient(
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return jsonify(message=message), 202


@identity_bp.get("/auth/verify-email")
def verify_email() -> tuple[Response, int]:
    """Activate the account from the link in the email. The token is single use."""
    _service().verify_email(request.args.get("token", ""))
    return jsonify(verified=True), 200


@identity_bp.get("/me")
@authenticated
def me() -> tuple[Response, int]:
    actor = g.actor
    return (
        jsonify(
            id=actor.user_id,
            roles=sorted(actor.roles),
            permissions=sorted(actor.permissions),
        ),
        200,
    )


@identity_bp.get("/patients/<uuid:patient_id>")
@authenticated
def get_patient(patient_id: Any) -> tuple[Response, int]:
    owner = _service().get_patient_profile(g.actor, str(patient_id))
    return jsonify(id=str(patient_id), usuario_id=owner), 200


@identity_bp.post("/users")
@authenticated
def create_user() -> tuple[Response, int]:
    body = _parse(CreateUserRequest)
    user_id = _service().create_user(
        g.actor,
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        roles=body.roles,
    )
    return jsonify(id=user_id), 201

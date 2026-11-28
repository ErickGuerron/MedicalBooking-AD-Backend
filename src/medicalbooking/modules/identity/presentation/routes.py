"""HTTP adapter for identity: /api/v1/auth, /api/v1/me, /api/v1/patients, /api/v1/users."""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from medicalbooking.modules.identity.application.ports import UserSummary
from medicalbooking.modules.identity.application.use_cases import ACTIVE, IdentityService
from medicalbooking.modules.identity.domain.policies import DEFAULT_PAGE_SIZE
from medicalbooking.shared.domain.errors import Unauthenticated, ValidationFailed

identity_bp = Blueprint("identity", __name__, url_prefix="/api/v1")
_INTEGER_PARAM = re.compile(r"-?[0-9]+")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    roles: list[str] = Field(min_length=1, max_length=4)
    # Required when roles includes MEDICO; ignored for other roles.
    registro_profesional: str | None = Field(default=None, max_length=80)


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


def _int_query(name: str, default: int) -> int:
    """Read an optional integer query parameter. Range rules belong to the use case."""
    if name not in request.args:
        return default
    raw = request.args[name]
    if not _INTEGER_PARAM.fullmatch(raw):
        raise ValidationFailed(f"'{name}' must be an integer")
    return int(raw)


def _user_json(user: UserSummary) -> dict[str, Any]:
    return {
        "id": user.user_id,
        "email": user.email,
        "nombres": user.first_name,
        "apellidos": user.last_name,
        "activo": user.status == ACTIVE,
        "roles": list(user.roles),
    }


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
    tipo_documento: Literal["CEDULA", "PASAPORTE"]
    numero_documento: str = Field(min_length=1, max_length=20)
    telefono: str | None = Field(default=None, max_length=20, description="Optional")
    sexo: Literal["M", "F"] | None = Field(default=None, description="Optional: M or F")
    fecha_nacimiento: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Optional YYYY-MM-DD, not in the future, age at most 120 years",
    )


@identity_bp.post("/auth/register")
def register() -> tuple[Response, int]:
    """Public patient self-registration. Always answers the same, whatever the email."""
    body = _parse(RegisterRequest)
    message = _service().register_patient(
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        document_type=body.tipo_documento,
        document_number=body.numero_documento,
        phone=body.telefono,
        sex=body.sexo,
        birth_date=body.fecha_nacimiento,
    )
    return jsonify(message=message), 202


@identity_bp.post("/auth/activate-account")
def activate_account() -> tuple[Response, int]:
    """Staff activation: the single-use link from the email, plus the password the person
    chooses."""
    body = _parse(ActivateAccountRequest)
    _service().activate_staff_account(body.token, body.password)
    return jsonify(activated=True), 200


@identity_bp.get("/auth/verify-email")
def verify_email() -> tuple[Response, int]:
    """Activate the account from the link in the email. The token is single use."""
    _service().verify_email(request.args.get("token", ""))
    return jsonify(verified=True), 200


@identity_bp.get("/auth/activation-status")
def activation_status() -> tuple[Response, int]:
    """Public: says whether an activation link can still be used, so the page refuses it at once."""
    return jsonify(status=_service().activation_status(request.args.get("token", ""))), 200


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)


@identity_bp.post("/auth/password-reset")
def request_password_reset() -> tuple[Response, int]:
    """Asks for a reset link. The answer never says whether the account exists."""
    body = _parse(PasswordResetRequest)
    _service().request_password_reset(body.email)
    return jsonify(
        message="If the address can receive an account, we sent a reset link to it."
    ), 202


@identity_bp.get("/auth/password-reset/status")
def password_reset_status() -> tuple[Response, int]:
    return jsonify(status=_service().password_reset_status(request.args.get("token", ""))), 200


@identity_bp.post("/auth/password-reset/confirm")
def confirm_password_reset() -> tuple[Response, int]:
    body = _parse(PasswordResetConfirm)
    _service().reset_password(body.token, body.password)
    return jsonify(reset=True), 200


class ActivateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@identity_bp.post("/me/password")
@authenticated
def change_password() -> tuple[str, int]:
    body = _parse(PasswordChangeRequest)
    _service().change_password(g.actor, body.current_password, body.new_password)
    return "", 204


@identity_bp.get("/me")
@authenticated
def me() -> tuple[Response, int]:
    actor = g.actor
    profile = _service().own_profile(actor)
    return (
        jsonify(
            id=actor.user_id,
            roles=sorted(actor.roles),
            permissions=sorted(actor.permissions),
            nombre=profile.first_name,
            apellido=profile.last_name,
            correo=profile.email,
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
        first_name=body.first_name,
        last_name=body.last_name,
        roles=body.roles,
        professional_registration=body.registro_profesional,
    )
    return jsonify(id=user_id), 201


@identity_bp.get("/users")
@authenticated
def list_users() -> tuple[Response, int]:
    users = _service().list_users(
        g.actor,
        limit=_int_query("limit", DEFAULT_PAGE_SIZE),
        offset=_int_query("offset", 0),
    )
    return jsonify(items=[_user_json(user) for user in users]), 200

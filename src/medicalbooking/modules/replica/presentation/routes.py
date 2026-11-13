"""HTTP adapter for the fallback read (spec 8.3, S8): the patient's results during an outage.

The route only maps the service outcome to HTTP. A denial is 403 and never carries data; an
audit that cannot be written is 503 and the read is not served (fail closed); a replica that
is not configured is 503. Responses are problem+json through the shared error handler.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any
from uuid import UUID

from flask import Blueprint, current_app, g, jsonify, request

from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AccountNotActive,
    AuditUnavailable,
)
from medicalbooking.shared.domain.errors import Forbidden, ServiceUnavailable, Unauthenticated

replica_bp = Blueprint("replica", __name__, url_prefix="/api/v1")


def _authenticated_without_sql(view: Callable[..., Any]) -> Callable[..., Any]:
    """Check the JWT signature only. The account itself is checked in the replica.

    The regular @authenticated decorator loads the account from SQL Server, which is exactly
    what is unavailable during the outage.
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not token:
            raise Unauthenticated("Authentication required")
        try:
            subject = current_app.extensions["medicalbooking.token_verifier"].subject_of(token)
        except Exception as error:
            raise Unauthenticated("Authentication required") from error
        g.actor_id = UUID(str(subject))
        return view(*args, **kwargs)

    return wrapper


@replica_bp.get("/me/resultados-contingencia")
@_authenticated_without_sql
def read_fallback_results():
    service = current_app.extensions.get("medicalbooking.fallback_read")
    if service is None:
        raise ServiceUnavailable("Fallback reads are not configured on this instance")
    try:
        result = service.results_for(
            actor_id=g.actor_id,
            correlation_id=uuid.uuid4(),
            ip=request.remote_addr,
        )
    except (AccessDenied, AccountNotActive) as error:
        raise Forbidden("Own results are not available for this account") from error
    except AuditUnavailable as error:
        raise ServiceUnavailable("The read was not served: audit is unavailable") from error
    return (
        jsonify(
            paciente_id=str(result.patient_id),
            ordenes=_plain(list(result.orders)),
            fuente="replica",
        ),
        200,
    )


def _plain(value: Any) -> Any:
    """JSON-safe copy: decimals become numbers, dates ISO strings, UUIDs strings."""
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value

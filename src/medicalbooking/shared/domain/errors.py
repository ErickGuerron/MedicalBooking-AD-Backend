"""Application errors mapped to HTTP problem responses by the presentation layer.

Messages are safe for clients: they never include credentials, tokens, SQL or
clinical data.
"""

from __future__ import annotations


class AppError(Exception):
    status: int = 500
    title: str = "Internal Server Error"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class Unauthenticated(AppError):
    status = 401
    title = "Unauthorized"


class Forbidden(AppError):
    status = 403
    title = "Forbidden"


class NotFound(AppError):
    status = 404
    title = "Not Found"


class Conflict(AppError):
    status = 409
    title = "Conflict"


class ValidationFailed(AppError):
    status = 422
    title = "Unprocessable Entity"


class ServiceUnavailable(AppError):
    status = 503
    title = "Service Unavailable"

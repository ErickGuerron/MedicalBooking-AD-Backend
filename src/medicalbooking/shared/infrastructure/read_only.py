"""Read-only mode: what the API says while the primary database cannot be reached.

Writes are refused with an explicit message; reads say the information is temporarily
unavailable. The driver message is never returned to the client, so server names and
connection details cannot leak.
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

READ_ONLY_CODE = "READ_ONLY_MODE"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WRITE_DETAIL = (
    "Actions are not available in read-only mode. Try again when the service is restored."
)
READ_DETAIL = "This information is temporarily unavailable. Try again shortly."


def is_database_unavailable(error: BaseException) -> bool:
    """True when the primary database cannot be reached or dropped the connection."""
    return isinstance(error, OperationalError)


def read_only_detail(method: str) -> str:
    return WRITE_DETAIL if method.upper() in WRITE_METHODS else READ_DETAIL

"""Reporting rules: the two read-only permissions and the period limits of the summary."""

from __future__ import annotations

from collections.abc import Container
from datetime import date

from medicalbooking.shared.domain.errors import Forbidden, ValidationFailed

PERM_REPORTS_READ = "reportes.leer"
PERM_MONITORING_READ = "monitoreo.leer"

# A summary covers at most one year, so one request cannot scan the whole history.
MAX_PERIOD_DAYS = 366


def require_permission(permissions: Container[str], permission: str) -> None:
    """Same answer as every other module for a missing permission: no hint about the resource."""
    if permission not in permissions:
        raise Forbidden("Access to this resource is not allowed")


def validate_period(desde: date, hasta: date) -> None:
    if desde > hasta:
        raise ValidationFailed("'desde' must be on or before 'hasta'")
    if (hasta - desde).days + 1 > MAX_PERIOD_DAYS:
        raise ValidationFailed(f"The period cannot be longer than {MAX_PERIOD_DAYS} days")

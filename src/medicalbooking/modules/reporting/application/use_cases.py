"""Administrator reports and monitoring use cases. Read-only: nothing here writes or emits."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.reporting.application.ports import (
    MonitoringReport,
    ReportingStore,
    ReportSummary,
)
from medicalbooking.modules.reporting.domain.policies import (
    PERM_MONITORING_READ,
    PERM_REPORTS_READ,
    require_permission,
    validate_period,
)


class ReportingService:
    def __init__(self, store: ReportingStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def summary(self, actor: AccessProfile, *, desde: date, hasta: date) -> ReportSummary:
        require_permission(actor.permissions, PERM_REPORTS_READ)
        validate_period(desde, hasta)
        return self._store.summary(desde, hasta)

    def monitoring(self, actor: AccessProfile) -> MonitoringReport:
        require_permission(actor.permissions, PERM_MONITORING_READ)
        return MonitoringReport(checked_at=self._clock(), snapshot=self._store.monitoring())

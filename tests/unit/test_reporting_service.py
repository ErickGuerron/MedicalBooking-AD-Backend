"""Administrator reports and monitoring: permissions, period rules and the read-only service."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.reporting.application.ports import (
    CountByState,
    MonitoringSnapshot,
    PaymentTotals,
    ReportSummary,
)
from medicalbooking.modules.reporting.application.use_cases import ReportingService
from medicalbooking.modules.reporting.domain.policies import (
    MAX_PERIOD_DAYS,
    PERM_MONITORING_READ,
    PERM_REPORTS_READ,
    validate_period,
)
from medicalbooking.shared.domain.errors import Forbidden, ValidationFailed

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
DESDE = date(2026, 11, 1)
HASTA = date(2026, 11, 30)


def _actor(*permissions: str) -> AccessProfile:
    return AccessProfile(
        user_id="admin",
        status="ACTIVO",
        roles=frozenset({"ADMINISTRADOR"}),
        permissions=frozenset(permissions),
    )


class FakeStore:
    """Records every call. The port has only read methods, so nothing can be written through it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def summary(self, desde: date, hasta: date) -> ReportSummary:
        self.calls.append(("summary", (desde, hasta)))
        return ReportSummary(
            desde=desde,
            hasta=hasta,
            citas=(CountByState("CONFIRMADA", 3),),
            ordenes=(CountByState("COMPLETADA", 1),),
            pagos=(PaymentTotals("CONFIRMADO", 2, Decimal("150.00")),),
            tratamientos=(),
        )

    def monitoring(self) -> MonitoringSnapshot:
        self.calls.append(("monitoring", ()))
        return MonitoringSnapshot(database_latency_ms=1.5, destinations=())


def _service(store: FakeStore | None = None) -> tuple[ReportingService, FakeStore]:
    fake = store or FakeStore()
    return ReportingService(fake, clock=lambda: NOW), fake


def test_summary_requires_reportes_leer() -> None:
    service, store = _service()
    with pytest.raises(Forbidden):
        service.summary(_actor(PERM_MONITORING_READ), desde=DESDE, hasta=HASTA)
    assert store.calls == []


def test_monitoring_requires_monitoreo_leer() -> None:
    service, store = _service()
    with pytest.raises(Forbidden):
        service.monitoring(_actor(PERM_REPORTS_READ))
    assert store.calls == []


def test_summary_passes_the_period_to_the_store() -> None:
    service, store = _service()
    result = service.summary(_actor(PERM_REPORTS_READ), desde=DESDE, hasta=HASTA)
    assert store.calls == [("summary", (DESDE, HASTA))]
    assert result.pagos[0].monto == Decimal("150.00")


def test_monitoring_stamps_the_check_time_from_the_clock() -> None:
    service, _ = _service()
    report = service.monitoring(_actor(PERM_MONITORING_READ))
    assert report.checked_at == NOW
    assert report.snapshot.database_latency_ms == 1.5


def test_a_period_of_one_day_is_valid() -> None:
    validate_period(DESDE, DESDE)


def test_a_period_ending_before_it_starts_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        validate_period(HASTA, DESDE)


def test_a_period_of_exactly_the_limit_is_accepted() -> None:
    start = date(2026, 1, 1)
    validate_period(start, start + timedelta(days=MAX_PERIOD_DAYS - 1))


def test_a_period_one_day_over_the_limit_is_rejected() -> None:
    start = date(2026, 1, 1)
    with pytest.raises(ValidationFailed):
        validate_period(start, start + timedelta(days=MAX_PERIOD_DAYS))


def test_the_service_rejects_an_invalid_period_before_reading() -> None:
    service, store = _service()
    with pytest.raises(ValidationFailed):
        service.summary(_actor(PERM_REPORTS_READ), desde=HASTA, hasta=DESDE)
    assert store.calls == []

"""Ports of the administrator reports and monitoring. The SQL adapter implements them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class CountByState:
    estado: str
    total: int


@dataclass(frozen=True)
class PaymentTotals:
    estado: str
    total: int
    monto: Decimal


@dataclass(frozen=True)
class ReportSummary:
    desde: date
    hasta: date
    citas: tuple[CountByState, ...]
    ordenes: tuple[CountByState, ...]
    pagos: tuple[PaymentTotals, ...]
    tratamientos: tuple[CountByState, ...]


@dataclass(frozen=True)
class DestinationBacklog:
    """Replication state of one active destination, read from the outbox deliveries."""

    codigo: str
    estados: tuple[CountByState, ...]
    pendiente_mas_antiguo: datetime | None
    ultima_entrega: datetime | None


@dataclass(frozen=True)
class MonitoringSnapshot:
    database_latency_ms: float
    destinations: tuple[DestinationBacklog, ...]


@dataclass(frozen=True)
class MonitoringReport:
    checked_at: datetime
    snapshot: MonitoringSnapshot


class ReportingStore(Protocol):
    def summary(self, desde: date, hasta: date) -> ReportSummary: ...

    def monitoring(self) -> MonitoringSnapshot: ...

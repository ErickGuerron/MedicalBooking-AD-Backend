"""SQL Server adapter for the reports and the monitoring. Read-only, parameterized SQL only.

Timestamps are compared against the half-open window [desde 00:00, hasta+1 00:00). The replication
state is read from the outbox deliveries, so a stalled destination shows as a growing backlog.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal

from sqlalchemy import Engine, text

from medicalbooking.modules.reporting.application.ports import (
    CountByState,
    DestinationBacklog,
    MonitoringSnapshot,
    PaymentTotals,
    ReportSummary,
)

_CITAS = (
    "SELECT [estado], COUNT(*) AS total FROM [citas] "
    "WHERE [reservada_en] >= :desde AND [reservada_en] < :hasta GROUP BY [estado] ORDER BY [estado]"
)
_ORDENES = (
    "SELECT [estado], COUNT(*) AS total FROM [ordenes_laboratorio] "
    "WHERE [fecha_orden] >= :desde AND [fecha_orden] < :hasta GROUP BY [estado] ORDER BY [estado]"
)
_PAGOS = (
    "SELECT [estado], COUNT(*) AS total, COALESCE(SUM([monto]), 0) AS monto FROM [pagos] "
    "WHERE [creado_en] >= :desde AND [creado_en] < :hasta GROUP BY [estado] ORDER BY [estado]"
)
_TRATAMIENTOS = (
    "SELECT [estado], COUNT(*) AS total FROM [tratamientos] "
    "WHERE [fecha_inicio] >= :desde AND [fecha_inicio] <= :hasta "
    "GROUP BY [estado] ORDER BY [estado]"
)
_DESTINATIONS = (
    "SELECT [codigo] FROM [destinos_sincronizacion] WHERE [activo] = 1 ORDER BY [codigo]"
)
_DELIVERY_COUNTS = (
    "SELECT d.[codigo], en.[estado], COUNT(*) AS total "
    "FROM [entregas_eventos] en JOIN [destinos_sincronizacion] d ON d.[id] = en.[destino_id] "
    "WHERE d.[activo] = 1 GROUP BY d.[codigo], en.[estado]"
)
_OLDEST_PENDING = (
    "SELECT d.[codigo], MIN(e.[creado_en]) AS mas_antiguo "
    "FROM [entregas_eventos] en "
    "JOIN [eventos_outbox] e ON e.[id] = en.[evento_id] "
    "JOIN [destinos_sincronizacion] d ON d.[id] = en.[destino_id] "
    "WHERE d.[activo] = 1 AND en.[estado] IN ('PENDIENTE', 'ERROR_TEMPORAL') GROUP BY d.[codigo]"
)
_LAST_DELIVERY = (
    "SELECT d.[codigo], MAX(en.[entregado_en]) AS ultima "
    "FROM [entregas_eventos] en JOIN [destinos_sincronizacion] d ON d.[id] = en.[destino_id] "
    "WHERE d.[activo] = 1 AND en.[estado] = 'ENTREGADO' GROUP BY d.[codigo]"
)


def _window(desde: date, hasta: date) -> tuple[datetime, datetime]:
    start = datetime.combine(desde, day_time.min)
    end = datetime.combine(hasta + timedelta(days=1), day_time.min)
    return start, end


class SqlReportingStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def summary(self, desde: date, hasta: date) -> ReportSummary:
        start, end = _window(desde, hasta)
        with self._engine.connect() as conn:
            citas = conn.execute(text(_CITAS), {"desde": start, "hasta": end}).all()
            ordenes = conn.execute(text(_ORDENES), {"desde": start, "hasta": end}).all()
            pagos = conn.execute(text(_PAGOS), {"desde": start, "hasta": end}).all()
            # tratamientos.fecha_inicio is a DATE column, so it compares with the plain dates.
            tratamientos = conn.execute(text(_TRATAMIENTOS), {"desde": desde, "hasta": hasta}).all()
        return ReportSummary(
            desde=desde,
            hasta=hasta,
            citas=tuple(CountByState(row.estado, int(row.total)) for row in citas),
            ordenes=tuple(CountByState(row.estado, int(row.total)) for row in ordenes),
            pagos=tuple(
                PaymentTotals(row.estado, int(row.total), Decimal(row.monto)) for row in pagos
            ),
            tratamientos=tuple(CountByState(row.estado, int(row.total)) for row in tratamientos),
        )

    def monitoring(self) -> MonitoringSnapshot:
        with self._engine.connect() as conn:
            started = time.perf_counter()
            conn.execute(text("SELECT 1"))
            latency_ms = round((time.perf_counter() - started) * 1000, 2)

            codes = [row.codigo for row in conn.execute(text(_DESTINATIONS))]
            counts: dict[str, list[CountByState]] = defaultdict(list)
            for row in conn.execute(text(_DELIVERY_COUNTS)):
                counts[row.codigo].append(CountByState(row.estado, int(row.total)))
            oldest = {
                row.codigo: _utc(row.mas_antiguo) for row in conn.execute(text(_OLDEST_PENDING))
            }
            last = {row.codigo: _utc(row.ultima) for row in conn.execute(text(_LAST_DELIVERY))}

        destinations = tuple(
            DestinationBacklog(
                codigo=code,
                estados=tuple(sorted(counts.get(code, []), key=lambda item: item.estado)),
                pendiente_mas_antiguo=oldest.get(code),
                ultima_entrega=last.get(code),
            )
            for code in codes
        )
        return MonitoringSnapshot(database_latency_ms=latency_ms, destinations=destinations)


def _utc(value: datetime | None) -> datetime | None:
    """SQL Server returns naive datetimes for datetime2 columns; the system stores them in UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC)

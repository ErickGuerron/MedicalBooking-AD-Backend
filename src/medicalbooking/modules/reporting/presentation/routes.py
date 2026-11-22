"""HTTP adapter for the administrator reports and monitoring. Maps service results to JSON only."""

from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, Response, current_app, g, jsonify, request

from medicalbooking.modules.identity.presentation.routes import authenticated
from medicalbooking.modules.reporting.application.ports import (
    CountByState,
    DestinationBacklog,
    MonitoringReport,
    PaymentTotals,
    ReportSummary,
)
from medicalbooking.modules.reporting.application.use_cases import ReportingService
from medicalbooking.shared.domain.errors import ValidationFailed

reporting_bp = Blueprint("reporting", __name__, url_prefix="/api/v1/admin")


def _service() -> ReportingService:
    return current_app.extensions["medicalbooking.reporting"]


def _date_query(name: str) -> date:
    raw = request.args.get(name, "")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise ValidationFailed(f"'{name}' must be a date in YYYY-MM-DD format") from error


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _counts(items: tuple[CountByState, ...]) -> list[dict[str, object]]:
    return [{"estado": item.estado, "total": item.total} for item in items]


def _payments(items: tuple[PaymentTotals, ...]) -> list[dict[str, object]]:
    # Money leaves as a string with two decimals, so no client rounds it through a float.
    return [
        {"estado": item.estado, "total": item.total, "monto": f"{item.monto:.2f}"} for item in items
    ]


def _summary_json(summary: ReportSummary) -> dict[str, object]:
    return {
        "desde": summary.desde.isoformat(),
        "hasta": summary.hasta.isoformat(),
        "citas": _counts(summary.citas),
        "ordenes_laboratorio": _counts(summary.ordenes),
        "pagos": _payments(summary.pagos),
        "tratamientos": _counts(summary.tratamientos),
    }


def _destination_json(item: DestinationBacklog) -> dict[str, object]:
    return {
        "codigo": item.codigo,
        "entregas": _counts(item.estados),
        "pendiente_mas_antiguo": _iso(item.pendiente_mas_antiguo),
        "ultima_entrega": _iso(item.ultima_entrega),
    }


def _monitoring_json(report: MonitoringReport) -> dict[str, object]:
    return {
        "revisado_en": report.checked_at.isoformat(),
        "base_principal": {
            "estado": "DISPONIBLE",
            "latencia_ms": report.snapshot.database_latency_ms,
        },
        "replicacion": [_destination_json(item) for item in report.snapshot.destinations],
    }


@reporting_bp.get("/reportes/resumen")
@authenticated
def reports_summary() -> tuple[Response, int]:
    summary = _service().summary(g.actor, desde=_date_query("desde"), hasta=_date_query("hasta"))
    return jsonify(_summary_json(summary)), 200


@reporting_bp.get("/monitoreo/estado")
@authenticated
def monitoring_status() -> tuple[Response, int]:
    report = _service().monitoring(g.actor)
    return jsonify(_monitoring_json(report)), 200

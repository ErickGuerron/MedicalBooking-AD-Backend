"""Refund eligibility rules (payments, refunds v1). Pure: no database, no gateway.

A refund is requested only for a paid appointment that the patient cancelled before it
started, at most once per payment, in full, within a window, with a reason from a closed
catalog. The first failing rule is reported by its code, so the caller can answer precisely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

REFUND_WINDOW_DAYS = 30
MAX_REQUESTS_PER_WINDOW = 2
REASON_CODES = frozenset({"CANCELACION_PACIENTE", "CANCELACION_MEDICO", "ERROR_DE_COBRO"})
_ACTIVE_REFUND_STATES = frozenset({"PENDIENTE", "PROCESADO"})

# Stripe refund status (refund.updated event) to reembolso outcome. Statuses not listed
# (pending, requires_action, unknown) leave the reembolso untouched.
REFUND_STATUS_OUTCOME = {
    "succeeded": "PROCESADO",
    "failed": "FALLIDO",
    "canceled": "FALLIDO",
}


class RefundRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RefundCase:
    pago_estado: str
    monto_pagado: Decimal
    pagado_en: datetime
    cita_estado: str
    cancelada_en: datetime | None
    cita_inicio: datetime
    consulta_atendida: bool
    reembolsos_activos: tuple[str, ...]
    solicitudes_recientes: int
    motivo_codigo: str
    monto_solicitado: Decimal


def check_refund_eligibility(case: RefundCase, now: datetime) -> None:
    """Raise RefundRefused with the first rule the request breaks; return None when eligible."""
    if case.pago_estado != "CONFIRMADO":
        raise RefundRefused("PAGO_NO_CONFIRMADO")
    if case.cita_estado != "CANCELADA":
        raise RefundRefused("CITA_NO_CANCELADA")
    if case.consulta_atendida:
        raise RefundRefused("CONSULTA_ATENDIDA")
    if case.cancelada_en is None or case.cancelada_en >= case.cita_inicio:
        raise RefundRefused("CANCELACION_TARDIA")
    if any(state in _ACTIVE_REFUND_STATES for state in case.reembolsos_activos):
        raise RefundRefused("REEMBOLSO_EXISTENTE")
    if now - case.pagado_en > timedelta(days=REFUND_WINDOW_DAYS):
        raise RefundRefused("FUERA_DE_VENTANA")
    if case.solicitudes_recientes >= MAX_REQUESTS_PER_WINDOW:
        raise RefundRefused("DEMASIADAS_SOLICITUDES")
    if case.motivo_codigo not in REASON_CODES:
        raise RefundRefused("MOTIVO_INVALIDO")
    if case.monto_solicitado != case.monto_pagado:
        raise RefundRefused("MONTO_NO_TOTAL")

"""Application service for the SOAP laboratory status operation (E6).

The access decision is not made here: it is the single-order read of the laboratory module,
so the SOAP operation and GET /api/v1/lab/orders/{id} can never disagree. This service only
projects the minimal status: no analytes, values, ranges, proposals or diagnoses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.use_cases import LabService


@dataclass(frozen=True)
class LaboratoryStatus:
    order_id: str
    status: str
    checked_at: datetime


class LaboratoryStatusService:
    def __init__(self, orders: LabService, clock: Callable[[], datetime]) -> None:
        self._orders = orders
        self._clock = clock

    def status_of(self, actor: AccessProfile, order_id: str) -> LaboratoryStatus:
        """Raise Forbidden for a missing or foreign order, exactly as the REST read does."""
        order = self._orders.get_order(actor, order_id)
        return LaboratoryStatus(order_id=order.id, status=order.estado, checked_at=self._clock())

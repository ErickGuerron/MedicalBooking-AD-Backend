"""The results PDF of an order for the outbox consumers, without the patient's session.

The notification worker sends the results as an attachment, so it builds the same report the
portal serves, from the order as it stands at send time. Nothing is stored.
"""

from __future__ import annotations

from datetime import datetime

from medicalbooking.modules.laboratory.application.ports import LabStore, ReportParties
from medicalbooking.modules.laboratory.application.results_report import build_results_report


def render_order_report(store: LabStore, order_id: str, generated_at: datetime) -> bytes | None:
    """The PDF of the order, or None when the order does not exist."""
    order = store.get_order(order_id)
    if order is None:
        return None
    parties = store.report_parties(order.id) or ReportParties(
        patient_name="—",
        patient_document=None,
        patient_birth_date=None,
        patient_sex=None,
        patient_email=None,
        patient_phone=None,
        doctor_name=None,
        doctor_registration=None,
    )
    return build_results_report(
        order,
        store.list_results(order.id),
        parties=parties,
        generated_at=generated_at,
    )

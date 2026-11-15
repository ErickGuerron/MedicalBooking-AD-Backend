"""Patient-requested lab exams: nullable ordering doctor, origin flag and the patient permission.

Revision ID: 0033_patient_requested_orders
Revises: 0032_service_requires_order
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0033_patient_requested_orders"
down_revision = "0032_service_requires_order"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0033_patient_requested_orders.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Patient orders have no doctor; reverting would either lose them or invent a doctor.
    raise NotImplementedError(
        "Reverting patient-requested orders is destructive; restore from a backup instead."
    )

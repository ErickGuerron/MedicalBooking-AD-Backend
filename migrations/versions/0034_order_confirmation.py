"""Order confirmation: the time the laboratory closed the order.

Revision ID: 0034_order_confirmation
Revises: 0033_patient_requested_orders
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0034_order_confirmation"
down_revision = "0033_patient_requested_orders"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0034_order_confirmation.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the confirmation moment would reopen closed orders; restore from a backup."
    )

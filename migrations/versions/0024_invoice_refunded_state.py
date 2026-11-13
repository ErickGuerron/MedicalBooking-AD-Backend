"""Invoice state REEMBOLSADA for fully refunded payments (payments, refunds v1).

Revision ID: 0024_invoice_refunded_state
Revises: 0023_kafka_destination
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0024_invoice_refunded_state"
down_revision = "0023_kafka_destination"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0024_invoice_refunded_state.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    raise NotImplementedError(
        "Removing REEMBOLSADA would violate existing refunded invoices; restore from a backup."
    )
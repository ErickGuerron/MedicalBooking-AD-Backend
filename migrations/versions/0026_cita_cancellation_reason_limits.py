"""Cancellation reason: 1500-character text and a SQL check on the reason code.

Revision ID: 0026_cita_cancellation_reason_limits
Revises: 0025_cita_cancellation_reason
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0026_cita_reason_limits"
down_revision = "0025_cita_cancellation_reason"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0026_cita_cancellation_reason_limits.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    raise NotImplementedError(
        "Removing REEMBOLSADA would violate existing refunded invoices; restore from a backup."
    )
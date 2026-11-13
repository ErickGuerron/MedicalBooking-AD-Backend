"""Doctor cancellation: reason columns on citas and the citas.cancelar_medico permission.

Revision ID: 0025_cita_cancellation_reason
Revises: 0024_invoice_refunded_state
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0025_cita_cancellation_reason"
down_revision = "0024_invoice_refunded_state"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0025_cita_cancellation_reason.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Stored cancellation reasons are history; dropping the columns would lose them.
    pass

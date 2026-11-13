"""Doctor cancellation limits: admin approvals and the citas.revisar_cancelaciones permission.

Revision ID: 0027_doctor_cancel_limits
Revises: 0026_cita_reason_limits
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0027_doctor_cancel_limits"
down_revision = "0026_cita_reason_limits"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0027_doctor_cancel_limits.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Approvals are an audit record of admin decisions: dropping them would erase who approved
    # a doctor's cancellation. Restore from a backup instead.
    raise NotImplementedError("Approval records are audit data; restore from a backup.")

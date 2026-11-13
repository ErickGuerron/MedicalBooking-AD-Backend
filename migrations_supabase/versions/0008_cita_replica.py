"""Appointments in the replica: the patient's own citas and their cancellation reason.

Revision ID: s0008_cita_replica
Revises: s0007_billing_replica
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0008_cita_replica"
down_revision = "s0007_billing_replica"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0008_cita_replica.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the appointment replica is destructive; restore from a backup instead."
    )

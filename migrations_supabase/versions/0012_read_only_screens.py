"""Read-only screens in the replica: doctors, specialties, services, appointment doctors.

Revision ID: s0012_read_only_screens
Revises: s0011_patient_orders_replica

The SQL file is the single source of the change.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0012_read_only_screens"
down_revision = "s0011_patient_orders_replica"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0012_read_only_screens.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the read-only screens would hide the doctor and service names during an outage."
    )

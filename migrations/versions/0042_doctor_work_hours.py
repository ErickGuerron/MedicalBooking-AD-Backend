"""Doctor work hours, service windows (morning sample collection, afternoon consultation) and permissions.

Revision ID: 0042_doctor_work_hours
Revises: 0041_reporting_permissions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0042_doctor_work_hours"
down_revision = "0041_reporting_permissions"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0042_doctor_work_hours.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: the table and granted permissions stay on downgrade; dropping hours a doctor
    # declared would lose data.
    pass

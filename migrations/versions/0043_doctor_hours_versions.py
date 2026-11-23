"""Doctor work hours as versions effective from a date: a change applies from a future week on.

Revision ID: 0043_doctor_hours_versions
Revises: 0042_doctor_work_hours
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0043_doctor_hours_versions"
down_revision = "0042_doctor_work_hours"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0043_doctor_hours_versions.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: the versions and the declared hours stay on downgrade; dropping them would lose data.
    pass

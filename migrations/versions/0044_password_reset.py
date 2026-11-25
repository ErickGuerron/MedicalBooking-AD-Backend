"""Password reset links: single use, one hour, stored only as a hash.

Revision ID: 0044_password_reset
Revises: 0043_doctor_hours_versions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0044_password_reset"
down_revision = "0043_doctor_hours_versions"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0044_password_reset.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Reset links are short-lived and carry no business data: dropping the table is safe.
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF OBJECT_ID(N'[restablecimientos_contrasena]', N'U') IS NOT NULL "
        "DROP TABLE [restablecimientos_contrasena]"
    )

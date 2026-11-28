"""Administrator reports and monitoring: read-only permissions for ADMINISTRADOR.

Revision ID: 0041_reporting_permissions
Revises: 0038_result_review
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0041_reporting_permissions"
down_revision = "0038_result_review"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0041_reporting_permissions.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: granted permissions stay on downgrade; removing them could lock out an admin.
    pass

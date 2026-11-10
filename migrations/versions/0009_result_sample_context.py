"""E5.1 sample context for laboratory results (fasting status). Adds one nullable column.

Revision ID: 0009_result_sample_context
Revises: 0008_email_verification
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0009_result_sample_context"
down_revision = "0008_email_verification"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0009_result_sample_context.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Dropping a result column would discard the recorded sample context.
    pass

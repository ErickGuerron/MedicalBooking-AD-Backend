"""E8 patient self-registration: email verification tokens.

Revision ID: 0008_email_verification
Revises: 0007_test_users
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0008_email_verification"
down_revision = "0007_test_users"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0008_email_verification.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Verification records are account evidence; dropping them would orphan audit history.
    pass

"""Checkout retry state on payment attempts (counters and schedule only).

Revision ID: 0021_checkout_retry
Revises: 0020_payment_methods
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0021_checkout_retry"
down_revision = "0020_payment_methods"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0021_checkout_retry.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Retry counters of attempts already processed are history; dropping them loses the audit.
    pass

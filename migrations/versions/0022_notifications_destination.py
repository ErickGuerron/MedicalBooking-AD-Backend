"""Synchronization destination NOTIFICACIONES for patient notifications (idempotent seed).

Revision ID: 0022_notifications_destination
Revises: 0021_checkout_retry
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0022_notifications_destination"
down_revision = "0021_checkout_retry"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0022_notifications_destination.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The destination may already hold delivery rows; removing it would orphan their history.
    pass

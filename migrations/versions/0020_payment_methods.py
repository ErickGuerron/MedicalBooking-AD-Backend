"""Saved payment methods: Stripe customer mapping and the card management permission.

Revision ID: 0020_payment_methods
Revises: 0019_pending_payment_per_invoice
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0020_payment_methods"
down_revision = "0019_pending_payment_per_invoice"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0020_payment_methods.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The mapping holds Stripe customers that may own saved cards; dropping it would orphan them.
    pass

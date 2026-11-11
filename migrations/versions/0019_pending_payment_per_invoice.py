"""One pending checkout attempt per invoice (filtered unique index on pagos).

Revision ID: 0019_pending_payment_per_invoice
Revises: 0018_billing_permissions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0019_pending_payment_per_invoice"
down_revision = "0018_billing_permissions"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0019_pending_payment_per_invoice.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The index guards concurrent checkouts; dropping it would reopen the race it closes.
    pass

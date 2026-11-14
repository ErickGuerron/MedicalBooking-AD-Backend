"""Billing in the replica: invoices, invoice lines, payments and refunds, least privilege.

Revision ID: s0007_billing_replica
Revises: s0006_reader_own_patient
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0007_billing_replica"
down_revision = "s0006_reader_own_patient"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0007_billing_replica.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the billing replica is destructive; restore from a backup instead."
    )

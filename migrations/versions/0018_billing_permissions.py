"""Billing permissions (invoices and checkout) and one invoice per appointment.

Revision ID: 0018_billing_permissions
Revises: 0017_vcm_source_and_analyte
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0018_billing_permissions"
down_revision = "0017_vcm_source_and_analyte"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0018_billing_permissions.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Permissions and the invoice index are kept: removing them could orphan granted rows.
    pass

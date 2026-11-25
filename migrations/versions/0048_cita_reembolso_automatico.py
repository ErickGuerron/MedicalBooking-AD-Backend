"""Records the outcome of the automatic refund of a cancelled visit.

Revision ID: 0048_cita_reembolso_automatico
Revises: 0047_password_reset
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0048_cita_reembolso_automatico"
down_revision = "0047_password_reset"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0048_cita_reembolso_automatico.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF COL_LENGTH(N'citas', N'reembolso_automatico') IS NOT NULL "
        "ALTER TABLE [citas] DROP COLUMN [reembolso_automatico]"
    )

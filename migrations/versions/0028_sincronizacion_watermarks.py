"""Watermarks for the external-write sync: one row per source table with the last delivered rowversion.

Revision ID: 0028_sincronizacion_watermarks
Revises: 0027_doctor_cancel_limits
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0028_sincronizacion_watermarks"
down_revision = "0027_doctor_cancel_limits"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0028_sincronizacion_watermarks.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Watermarks only record sync progress; dropping them would replay every row on the next run.
    raise NotImplementedError("Watermarks record sync progress; restore from a backup instead.")

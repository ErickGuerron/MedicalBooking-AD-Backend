"""Baseline schema from the reviewed T-SQL script.

Revision ID: 0001_baseline
Revises:
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0001_initial_schema.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    raise NotImplementedError(
        "Baseline downgrade is intentionally unsupported: restore from backup instead."
    )

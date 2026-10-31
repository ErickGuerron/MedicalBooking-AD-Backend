"""Idempotent reference data (roles, synchronization targets).

Revision ID: 0002_seed_reference
Revises: 0001_baseline
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0002_seed_reference"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0002_seed_reference_data.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Seeds are reference data that other rows may depend on; removing them is unsafe.
    pass

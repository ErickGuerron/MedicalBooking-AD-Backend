"""Supabase replica schema: projections, idempotency evidence and lag watermarks.

Revision ID: s0001_replica_schema
Revises:
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0001_replica_schema"
down_revision = None
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0001_replica_schema.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the replica schema is destructive; restore from a backup instead."
    )

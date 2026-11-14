"""Treatments and follow-up notes in the replica: tables, grants and row-level security.

Revision ID: s0010_treatments_replica
Revises: s0009_replica_delete_grants

Creates two replica tables only. The SQL file is the single source of the change.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0010_treatments_replica"
down_revision = "s0009_replica_delete_grants"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0010_treatments_replica.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the treatment replica is destructive; restore from a backup instead."
    )

"""Replica roles privileges and row-level security (authoritative, idempotent).

Revision ID: s0002_replica_roles_rls
Revises: s0001_replica_schema
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0002_replica_roles_rls"
down_revision = "s0001_replica_schema"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0002_replica_roles_and_rls.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Privileges follow the design; change the SQL file and upgrade instead of downgrading."
    )

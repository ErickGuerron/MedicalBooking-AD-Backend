"""Reader policies fail closed with zero rows when no patient is set.

Revision ID: s0003_reader_policy_nullif
Revises: s0002_replica_roles_rls
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0003_reader_policy_nullif"
down_revision = "s0002_replica_roles_rls"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0003_reader_policy_nullif.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "The previous policies raised a cast error on an empty patient; restore from a backup."
    )

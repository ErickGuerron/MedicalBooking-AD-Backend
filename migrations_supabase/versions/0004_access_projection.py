"""Access tables, patient account link and reviewed clinical projection for the replica.

Revision ID: s0004_access_projection
Revises: s0003_reader_policy_nullif
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0004_access_projection"
down_revision = "s0003_reader_policy_nullif"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0004_access_and_clinical_projection.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping the access projection is destructive; restore from a backup instead."
    )

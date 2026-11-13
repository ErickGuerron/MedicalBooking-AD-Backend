"""The consumer reads the processed history to stay idempotent; it still cannot change it.

Revision ID: s0005_writer_reads_history
Revises: s0004_access_projection
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0005_writer_reads_history"
down_revision = "s0004_access_projection"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0005_writer_reads_processed_history.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Re-revoking the writer read would break the consumer's idempotency.")

"""The reader finds its own patient from the account, and nothing else.

Revision ID: s0006_reader_own_patient
Revises: s0005_writer_reads_history
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0006_reader_own_patient"
down_revision = "s0005_writer_reads_history"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0006_reader_finds_own_patient.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Dropping the self-lookup policy would break patient resolution for the fallback read.")

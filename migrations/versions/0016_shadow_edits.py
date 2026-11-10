"""Physician edits of shadow findings: append-only table with immutability trigger.

Revision ID: 0016_shadow_edits
Revises: 0015_result_pregnancy_state
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0016_shadow_edits"
down_revision = "0015_result_pregnancy_state"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0016_shadow_edits.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The table holds clinical review records; dropping it would destroy them.
    raise NotImplementedError("Shadow edits downgrade drops clinical review records.")

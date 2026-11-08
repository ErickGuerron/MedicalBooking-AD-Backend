"""Source row for the WHO 2020 ferritin guideline (iron deficiency cut-offs, Table 1).

Revision ID: 0011_who_ferritin_source
Revises: 0010_synthetic_glucose_ayuno
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0011_who_ferritin_source"
down_revision = "0010_synthetic_glucose_ayuno"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0011_who_ferritin_source.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Sources are cited by references; removing one would orphan published knowledge.
    pass

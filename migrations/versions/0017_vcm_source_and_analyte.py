"""Source and analyte for the MCV supporting signal (S20).

Revision ID: 0017_vcm_source_and_analyte
Revises: 0016_shadow_edits
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0017_vcm_source_and_analyte"
down_revision = "0016_shadow_edits"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0017_vcm_source_and_analyte.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Source and analyte are data that other rows may reference; they are not removed.
    pass

"""Clinical history catalogs: CIE-11 codes, vaccines and allergens that entries must reference.

Revision ID: 0046_historia_catalogos
Revises: 0045_historia_clinica
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0046_historia_catalogos"
down_revision = "0045_historia_clinica"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0046_historia_catalogos.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: catalogs and their links hold medical codes already recorded; never dropped here.
    pass

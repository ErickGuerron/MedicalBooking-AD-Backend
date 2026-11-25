"""Clinical history: verified allergies, antecedents, medication, problems, vaccines, vital signs.

Revision ID: 0045_historia_clinica
Revises: 0044_sedes_admin
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0045_historia_clinica"
down_revision = "0044_sedes_admin"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0045_historia_clinica.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: clinical entries are medical records; a downgrade must never drop them.
    pass

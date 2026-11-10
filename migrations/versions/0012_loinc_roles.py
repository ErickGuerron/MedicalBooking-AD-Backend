"""LOINC codes for the shadow findings: release synthetic codes, assign verified codes.

Revision ID: 0012_loinc_roles
Revises: 0011_who_ferritin_source
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0012_loinc_roles"
down_revision = "0011_who_ferritin_source"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0012_loinc_roles.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Codes are data, not structure; reverting them would bring back the code collisions.
    pass

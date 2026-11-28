"""Permission to create and edit sedes and consultorios (sedes.administrar, for ADMINISTRADOR).

Revision ID: 0044_sedes_admin
Revises: 0043_doctor_hours_versions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0044_sedes_admin"
down_revision = "0043_doctor_hours_versions"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0044_sedes_admin.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: the permission stays on downgrade; removing it would strip admins of access
    # they were granted. Remove it by hand if the feature is withdrawn.
    pass

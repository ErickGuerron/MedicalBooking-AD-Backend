"""Lipid profile: total cholesterol and triglycerides as analytes of LIPI-SINT.

Revision ID: 0037_lipid_profile_analytes
Revises: 0036_service_dependency
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0037_lipid_profile_analytes"
down_revision = "0036_service_dependency"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0037_lipid_profile_analytes.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # shortcut: data-only migration; analytes may already be referenced by results, so they stay.
    pass

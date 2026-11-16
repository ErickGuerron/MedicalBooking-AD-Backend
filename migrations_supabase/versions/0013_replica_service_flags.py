"""Replica: the service flags (medical order and blood sample) for the read-only screens.

Revision ID: s0013_replica_service_flags
Revises: s0012_read_only_screens

The SQL file is the single source of the change.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0013_replica_service_flags"
down_revision = "s0012_read_only_screens"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0013_replica_service_flags.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE replica.servicios DROP COLUMN IF EXISTS requiere_toma_sangre; "
        "ALTER TABLE replica.servicios DROP COLUMN IF EXISTS requiere_orden_medica;"
    )

"""Replica: the service dependency replaces the blood sample flag.

Revision ID: s0014_replica_service_dependency
Revises: s0013_replica_service_flags

The SQL file is the single source of the change.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0014_replica_service_dependency"
down_revision = "s0013_replica_service_flags"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0014_replica_service_dependency.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE replica.servicios DROP COLUMN IF EXISTS servicio_adicional_id; "
        "ALTER TABLE replica.servicios "
        "ADD COLUMN IF NOT EXISTS requiere_toma_sangre boolean NOT NULL DEFAULT false;"
    )

"""Replica writer may delete patient profiles and appointments (usuario.eliminado, cita.eliminada).

Revision ID: s0009_replica_delete_grants
Revises: s0008_cita_replica
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0009_replica_delete_grants"
down_revision = "s0008_cita_replica"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0009_replica_delete_grants.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Revokes only the two privileges this revision added; no data is touched.
    op.get_bind().exec_driver_sql(
        "REVOKE DELETE ON replica.pacientes, replica.citas FROM replica_writer"
    )

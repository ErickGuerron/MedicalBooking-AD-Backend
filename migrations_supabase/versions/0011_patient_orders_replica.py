"""Patient-requested lab orders in the replica: nullable doctor and the origin column.

Revision ID: s0011_patient_orders_replica
Revises: s0010_treatments_replica

The SQL file is the single source of the change.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "s0011_patient_orders_replica"
down_revision = "s0010_treatments_replica"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0011_patient_orders_replica.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "Reverting the replica origin column would drop patient orders; restore from a backup."
    )

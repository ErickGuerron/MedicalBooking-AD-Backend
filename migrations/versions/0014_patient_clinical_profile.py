"""Patient clinical profile for the WHO 2024 adjustments: columns, constraints and permission.

Revision ID: 0014_patient_clinical_profile
Revises: 0013_shadow_snapshots
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0014_patient_clinical_profile"
down_revision = "0013_shadow_snapshots"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0014_patient_clinical_profile.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The columns hold patient data; dropping them would destroy it. Restore from backup instead.
    raise NotImplementedError("Patient clinical profile downgrade drops patient data.")

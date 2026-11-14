"""Pregnancy state at sample time on lab results: column and check constraint.

Revision ID: 0015_result_pregnancy_state
Revises: 0014_patient_clinical_profile
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0015_result_pregnancy_state"
down_revision = "0014_patient_clinical_profile"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0015_result_pregnancy_state.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # The column holds clinical data recorded with results; dropping it would destroy it.
    raise NotImplementedError("Result pregnancy state downgrade drops clinical data.")

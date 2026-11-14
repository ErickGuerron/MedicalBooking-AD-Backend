"""Synthetic glucose fixture reference is a fasting reference (contexto AYUNO).

Revision ID: 0010_synthetic_glucose_ayuno
Revises: 0009_result_sample_context
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0010_synthetic_glucose_ayuno"
down_revision = "0009_result_sample_context"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0010_synthetic_glucose_ayuno.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "UPDATE [conocimiento_referencias] SET [contexto] = NULL "
        "WHERE [id] = 'c0000000-0000-4000-8000-00000000d002' AND [contexto] = 'AYUNO'"
    )

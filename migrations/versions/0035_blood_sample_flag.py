"""Services: the flag for the lab exams that need a blood sample.

Revision ID: 0035_blood_sample_flag
Revises: 0034_order_confirmation
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0035_blood_sample_flag"
down_revision = "0034_order_confirmation"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0035_blood_sample_flag.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF COL_LENGTH('servicios_medicos', 'requiere_toma_sangre') IS NOT NULL "
        "BEGIN "
        "ALTER TABLE servicios_medicos DROP CONSTRAINT DF_servicios_medicos_requiere_toma_sangre; "
        "ALTER TABLE servicios_medicos DROP COLUMN requiere_toma_sangre; "
        "END"
    )

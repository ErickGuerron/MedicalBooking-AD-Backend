"""Services: a dependent service replaces the blood sample flag.

Revision ID: 0036_service_dependency
Revises: 0035_blood_sample_flag
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0036_service_dependency"
down_revision = "0035_blood_sample_flag"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0036_service_dependency.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF COL_LENGTH('servicios_medicos', 'requiere_toma_sangre') IS NULL "
        "BEGIN "
        "ALTER TABLE servicios_medicos ADD requiere_toma_sangre BIT NOT NULL "
        "CONSTRAINT DF_servicios_medicos_requiere_toma_sangre DEFAULT 0; "
        "END"
    )
    connection.exec_driver_sql(
        "UPDATE servicios_medicos SET requiere_toma_sangre = 1 "
        "WHERE servicio_adicional_id IS NOT NULL"
    )
    connection.exec_driver_sql(
        "IF COL_LENGTH('servicios_medicos', 'servicio_adicional_id') IS NOT NULL "
        "BEGIN "
        "ALTER TABLE servicios_medicos DROP CONSTRAINT FK_servicios_medicos_servicio_adicional; "
        "ALTER TABLE servicios_medicos DROP COLUMN servicio_adicional_id; "
        "END"
    )

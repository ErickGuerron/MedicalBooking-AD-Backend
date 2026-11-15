"""Services: the flag for the services that need a medical order (result reviews).

Revision ID: 0032_service_requires_order
Revises: 0031_consultation_permission
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0032_service_requires_order"
down_revision = "0031_consultation_permission"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0032_service_requires_order.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF COL_LENGTH('servicios_medicos', 'requiere_orden_medica') IS NOT NULL "
        "BEGIN "
        "ALTER TABLE servicios_medicos DROP CONSTRAINT DF_servicios_medicos_requiere_orden; "
        "ALTER TABLE servicios_medicos DROP COLUMN requiere_orden_medica; "
        "END"
    )

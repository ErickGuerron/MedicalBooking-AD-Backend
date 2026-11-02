"""E3 laboratory: synthetic test catalog.

Revision ID: 0005_lab_test_catalog
Revises: 0004_login_lockout
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0005_lab_test_catalog"
down_revision = "0004_login_lockout"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0005_lab_test_catalog.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Only synthetic catalog rows are removed; no orders or results reference them yet
    # in a fresh environment, and the FK would block removal otherwise.
    connection = op.get_bind()
    connection.exec_driver_sql(
        "DELETE FROM [tipos_prueba_laboratorio] WHERE [codigo] IN "
        "('HEMO-SINT', 'GLUC-SINT', 'LIPI-SINT') "
        "AND NOT EXISTS (SELECT 1 FROM [ordenes_laboratorio_detalle] d "
        "WHERE d.[tipo_prueba_id] = [tipos_prueba_laboratorio].[id])"
    )

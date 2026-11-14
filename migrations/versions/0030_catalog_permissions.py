"""Catalog administration: the catalogo.administrar permission for ADMINISTRADOR.

Revision ID: 0030_catalog_permissions
Revises: 0029_treatments
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0030_catalog_permissions"
down_revision = "0029_treatments"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0030_catalog_permissions.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "DELETE rp FROM roles_permisos rp JOIN permisos p ON p.id = rp.permiso_id "
        "WHERE p.codigo = 'catalogo.administrar'"
    )
    connection.exec_driver_sql("DELETE FROM permisos WHERE codigo = 'catalogo.administrar'")

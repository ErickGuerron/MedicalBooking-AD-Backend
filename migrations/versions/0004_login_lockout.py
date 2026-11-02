"""E1 hardening: failed-login counter (brute-force protection).

Revision ID: 0004_login_lockout
Revises: 0003_identity_permissions
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0004_login_lockout"
down_revision = "0003_identity_permissions"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0004_login_lockout.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Removing the counter only forgets failure counts; no credential or account data is lost.
    connection = op.get_bind()
    connection.exec_driver_sql(
        "IF COL_LENGTH(N'dbo.credenciales_usuario', N'intentos_fallidos') IS NOT NULL "
        "BEGIN "
        "ALTER TABLE [credenciales_usuario] DROP CONSTRAINT [CK_credenciales_intentos]; "
        "ALTER TABLE [credenciales_usuario] DROP CONSTRAINT [DF_credenciales_intentos]; "
        "ALTER TABLE [credenciales_usuario] DROP COLUMN [intentos_fallidos]; "
        "END"
    )

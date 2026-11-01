"""E1 identity: credentials table and permission matrix.

Revision ID: 0003_identity_permissions
Revises: 0002_seed_reference
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0003_identity_permissions"
down_revision = "0002_seed_reference"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0003_identity_permissions.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Dropping credentials would lock out every account; restore from backup instead.
    raise NotImplementedError("Identity migration downgrade is intentionally unsupported.")

"""E5 clinical knowledge: permissions and synthetic approved seed.

Revision ID: 0006_clinical_knowledge_seed
Revises: 0005_lab_test_catalog
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0006_clinical_knowledge_seed"
down_revision = "0005_lab_test_catalog"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0006_clinical_knowledge_seed.sql"


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Knowledge can be referenced by results and proposals once used. Removing it would orphan
    # clinical history, so the seed and permissions are kept on downgrade by design.
    pass

"""Immutable snapshots of the shadow findings (feature doc S9): tables and immutability triggers.

Revision ID: 0013_shadow_snapshots
Revises: 0012_loinc_roles
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0013_shadow_snapshots"
down_revision = "0012_loinc_roles"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0013_shadow_snapshots.sql"
_DROP_STATEMENTS = (
    "DROP TRIGGER [TR_evidencias_inmutables]",
    "DROP TRIGGER [TR_items_inmutables]",
    "DROP TRIGGER [TR_snapshots_inmutables]",
    "ALTER TABLE [hallazgos_sombra_evidencias] DROP CONSTRAINT [FK_sombra_evidencias_resultado]",
    "ALTER TABLE [hallazgos_sombra_evidencias] DROP CONSTRAINT [FK_sombra_evidencias_item]",
    "ALTER TABLE [hallazgos_sombra_items] DROP CONSTRAINT [FK_items_snapshot]",
    "ALTER TABLE [hallazgos_sombra_snapshots] DROP CONSTRAINT [FK_snapshots_usuario]",
    "ALTER TABLE [hallazgos_sombra_snapshots] DROP CONSTRAINT [FK_snapshots_orden]",
    "DROP TABLE [hallazgos_sombra_evidencias]",
    "DROP TABLE [hallazgos_sombra_items]",
    "DROP TABLE [hallazgos_sombra_snapshots]",
)


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_SQL_FILE.read_text(encoding="utf-8")):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Snapshots are clinical records. The triggers block UPDATE and DELETE, so they are removed
    # first; dropping the tables is only meant for a development database.
    connection = op.get_bind()
    for statement in _DROP_STATEMENTS:
        connection.exec_driver_sql(statement)

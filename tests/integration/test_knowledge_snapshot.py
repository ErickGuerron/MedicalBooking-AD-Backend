"""The knowledge snapshot from SQL Server: every table, deterministic, and the gate's provider."""

from __future__ import annotations

from sqlalchemy import Engine

from medicalbooking.modules.clinical.domain.knowledge_digest import TABLES
from medicalbooking.modules.clinical.infrastructure.knowledge_snapshot import (
    database_knowledge_digest,
    digest_provider,
    read_knowledge_tables,
)


def test_every_knowledge_table_is_read(migrated_engine: Engine) -> None:
    tables = read_knowledge_tables(migrated_engine)

    assert set(tables) == {spec.name for spec in TABLES}


def test_the_digest_is_the_same_on_every_read(migrated_engine: Engine) -> None:
    first = database_knowledge_digest(migrated_engine).digest
    second = database_knowledge_digest(migrated_engine).digest

    assert first == second


def test_the_gate_provider_reports_the_database_digest(migrated_engine: Engine) -> None:
    assert digest_provider(migrated_engine)() == database_knowledge_digest(migrated_engine).digest

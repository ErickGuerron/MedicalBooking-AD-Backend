"""Supabase replica deletes need DELETE grants; they live in the migration chain (no live database).

The checks read the migration files: the Alembic chain must have one head (0009), that head must
grant DELETE on exactly the two tables the grant was approved for, and every table the Supabase
sink deletes from must hold a DELETE grant for replica_writer somewhere in the chain.
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
SUPABASE = ROOT / "migrations_supabase"
SQL_DIR = SUPABASE / "sql"
SINK = ROOT / "src" / "medicalbooking" / "modules" / "replica" / "infrastructure"
HEAD = "s0010_treatments_replica"
GRANT = re.compile(r"GRANT\s+(.*?)\s+ON\s+(.*?)\s+TO\s+(\w+)\s*;", re.DOTALL | re.IGNORECASE)


def _without_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _grants(sql: str) -> list[tuple[set[str], set[str], str]]:
    """Each GRANT as (privileges, tables, role), comments excluded."""
    grants = []
    for match in GRANT.finditer(_without_comments(sql)):
        privileges = {item.strip().upper() for item in match[1].split(",")}
        tables = {item.strip() for item in match[2].split(",")}
        grants.append((privileges, tables, match[3]))
    return grants


def test_the_supabase_revision_chain_has_one_head_and_it_is_0010():
    heads = ScriptDirectory(str(SUPABASE)).get_heads()

    assert heads == [HEAD]


def test_migration_0009_grants_delete_only_on_the_profile_and_the_appointment():
    sql = (SQL_DIR / "0009_replica_delete_grants.sql").read_text(encoding="utf-8")

    assert _without_comments(sql).upper().count("GRANT") == 1
    assert _grants(sql) == [({"DELETE"}, {"replica.pacientes", "replica.citas"}, "replica_writer")]


def test_every_table_the_supabase_sink_deletes_from_is_granted_delete_in_the_chain():
    granted: set[str] = set()
    for path in sorted(SQL_DIR.glob("*.sql")):
        for privileges, tables, role in _grants(path.read_text(encoding="utf-8")):
            if role == "replica_writer" and "DELETE" in privileges:
                granted |= tables
    sink = (SINK / "supabase_projection.py").read_text(encoding="utf-8")
    deleted = set(re.findall(r"DELETE FROM (replica\.\w+)", sink))

    assert deleted, "the sink must delete from at least one table"
    assert deleted <= granted, f"no DELETE grant for {sorted(deleted - granted)}"

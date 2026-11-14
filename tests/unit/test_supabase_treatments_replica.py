"""Supabase replica for tratamientos and seguimientos (treatments S11), checked without a database.

The migration file is read, never run. The checks cover the revision chain, the two replica tables
with their replicated columns and the estado CHECK, the grants (the reader only selects; the writer
never deletes), and the SQL the projection sink sends for each event.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory

from medicalbooking.modules.replica.infrastructure.supabase_projection import _Transaction

ROOT = Path(__file__).resolve().parents[2]
SUPABASE = ROOT / "migrations_supabase"
SQL_FILE = SUPABASE / "sql" / "0010_treatments_replica.sql"
REVISION = "s0010_treatments_replica"
PREVIOUS = "s0009_replica_delete_grants"
TRATAMIENTOS = "replica.tratamientos"
SEGUIMIENTOS = "replica.tratamientos_seguimientos"
NEW_TABLES = {TRATAMIENTOS, SEGUIMIENTOS}
GRANT = re.compile(r"GRANT\s+(.*?)\s+ON\s+(.*?)\s+TO\s+(\w+)\s*;", re.DOTALL | re.IGNORECASE)
TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (replica\.\w+) \((.*?)\n\);", re.DOTALL)


def _without_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _sql() -> str:
    return SQL_FILE.read_text(encoding="utf-8")


def _grants(sql: str) -> list[tuple[set[str], set[str], str]]:
    grants = []
    for match in GRANT.finditer(_without_comments(sql)):
        privileges = {item.strip().upper() for item in match[1].split(",")}
        tables = {item.strip() for item in match[2].split(",")}
        grants.append((privileges, tables, match[3]))
    return grants


def _privileges(sql: str, role: str, table: str) -> set[str]:
    found: set[str] = set()
    for privileges, tables, grantee in _grants(sql):
        if grantee == role and table in tables:
            found |= privileges
    return found


def _table_bodies(sql: str) -> dict[str, str]:
    return {name: body for name, body in TABLE.findall(_without_comments(sql))}


def test_the_revision_chain_head_is_0010_and_follows_0009() -> None:
    script = ScriptDirectory(str(SUPABASE))

    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def test_the_revision_file_runs_the_new_sql_file() -> None:
    module = (SUPABASE / "versions" / "0010_treatments_replica.py").read_text(encoding="utf-8")

    assert SQL_FILE.name in module
    assert SQL_FILE.exists()


def test_both_tables_hold_the_replicated_columns_and_a_uuid_primary_key() -> None:
    bodies = _table_bodies(_sql())

    assert set(bodies) == NEW_TABLES
    assert re.search(r"\bid uuid PRIMARY KEY\b", bodies[TRATAMIENTOS])
    assert re.search(r"\bid uuid PRIMARY KEY\b", bodies[SEGUIMIENTOS])
    for column in (
        "cita_id",
        "paciente_id",
        "medico_id",
        "descripcion",
        "estado",
        "fecha_inicio",
        "fecha_fin",
        "version",
    ):
        assert re.search(rf"\b{column}\b", bodies[TRATAMIENTOS]), column
    for column in ("tratamiento_id", "secuencia", "nota", "creado_en"):
        assert re.search(rf"\b{column}\b", bodies[SEGUIMIENTOS]), column


def test_no_audit_or_professional_column_reaches_the_replica_tables() -> None:
    bodies = _table_bodies(_sql())

    for body in bodies.values():
        for forbidden in ("registrado_por", "medico_user_id", "paciente_user_id", "actualizado_en"):
            assert not re.search(rf"\b{forbidden}\b", body), forbidden


def test_the_estado_check_allows_only_the_three_treatment_states() -> None:
    body = _table_bodies(_sql())[TRATAMIENTOS]

    assert "CHECK (estado IN ('ACTIVO', 'FINALIZADO', 'SUSPENDIDO'))" in body


def test_the_reader_role_only_selects_from_the_new_tables() -> None:
    sql = _sql()

    for table in NEW_TABLES:
        assert _privileges(sql, "replica_reader", table) == {"SELECT"}, table
    reader_grants = [g for g in _grants(sql) if g[2] == "replica_reader"]
    assert all(privileges == {"SELECT"} for privileges, _, _ in reader_grants)


def test_the_writer_role_inserts_and_updates_treatments_and_only_inserts_follow_ups() -> None:
    sql = _sql()

    assert _privileges(sql, "replica_writer", TRATAMIENTOS) == {"SELECT", "INSERT", "UPDATE"}
    assert _privileges(sql, "replica_writer", SEGUIMIENTOS) == {"INSERT"}


def test_no_role_may_delete_from_the_new_tables() -> None:
    for privileges, tables, _ in _grants(_sql()):
        if tables & NEW_TABLES:
            assert "DELETE" not in privileges


def test_the_migration_only_grants_on_the_new_tables() -> None:
    for _, tables, _ in _grants(_sql()):
        assert tables <= NEW_TABLES


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.statements.append((" ".join(sql.split()), params))


def test_a_treatment_upsert_keeps_only_a_newer_version() -> None:
    cursor = _Cursor()
    tratamiento = {
        "id": "11111111-1111-4111-8111-111111111111",
        "cita_id": "33333333-3333-4333-8333-333333333333",
        "paciente_id": "44444444-4444-4444-8444-444444444444",
        "medico_id": "55555555-5555-4555-8555-555555555555",
        "descripcion": "Control a las dos semanas (sintetico)",
        "estado": "ACTIVO",
        "fecha_inicio": "2026-11-01",
        "fecha_fin": None,
    }

    _Transaction(cursor).upsert_tratamiento(tratamiento, 4)

    sql, params = cursor.statements[-1]
    assert sql.startswith("INSERT INTO replica.tratamientos")
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "WHERE replica.tratamientos.version < excluded.version" in sql
    assert params[-1] == 4
    assert params[4] == "Control a las dos semanas (sintetico)"


def test_a_follow_up_insert_needs_only_insert_privilege_and_keeps_the_full_note() -> None:
    cursor = _Cursor()
    seguimiento = {
        "id": "22222222-2222-4222-8222-222222222222",
        "tratamiento_id": "11111111-1111-4111-8111-111111111111",
        "secuencia": 7,
        "nota": "Paciente refiere mejoria del dolor (sintetico).",
        "creado_en": "2026-11-14T12:00:00+00:00",
    }

    _Transaction(cursor).upsert_seguimiento(seguimiento)

    sql, params = cursor.statements[-1]
    assert sql.startswith("INSERT INTO replica.tratamientos_seguimientos")
    # ON CONFLICT would need SELECT, which replica_writer does not hold on this table.
    assert "ON CONFLICT" not in sql
    assert params == (
        seguimiento["id"],
        seguimiento["tratamiento_id"],
        7,
        seguimiento["nota"],
        seguimiento["creado_en"],
    )

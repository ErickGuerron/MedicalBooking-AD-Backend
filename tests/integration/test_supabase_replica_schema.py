"""Structure of the Supabase replica after revision s0004 (owner connection, read-only checks).

Runs only when SUPABASE_DATABASE_URL is set. It verifies columns, never data.
"""

import os

import psycopg
import pytest

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get("SUPABASE_DATABASE_URL")
requires_owner = pytest.mark.skipif(not OWNER_URL, reason="SUPABASE_DATABASE_URL is not set")


def _columns(cur, table: str) -> set[str]:
    cur.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'replica' and table_name = %s",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


@requires_owner
@pytest.mark.parametrize(
    "table",
    [
        "usuarios",
        "usuarios_roles",
        "roles",
        "roles_permisos",
        "permisos",
        "propuestas_clinicas",
        "propuestas_hallazgos",
    ],
)
def test_access_and_clinical_tables_exist(table):
    with psycopg.connect(OWNER_URL, connect_timeout=15) as conn, conn.cursor() as cur:
        assert _columns(cur, table), f"replica.{table} is missing"


@requires_owner
def test_the_replica_account_holds_only_id_and_state():
    with psycopg.connect(OWNER_URL, connect_timeout=15) as conn, conn.cursor() as cur:
        assert _columns(cur, "usuarios") == {"id", "estado", "actualizado_en"}


@requires_owner
def test_patients_carry_the_account_link_and_birth_date_but_no_contact_data():
    with psycopg.connect(OWNER_URL, connect_timeout=15) as conn, conn.cursor() as cur:
        columns = _columns(cur, "pacientes")
    assert {"usuario_id", "fecha_nacimiento"} <= columns
    assert not columns & {"email", "telefono", "numero_documento", "tipo_documento"}


@requires_owner
def test_the_clinical_projection_has_no_pending_or_rejected_state_column():
    with psycopg.connect(OWNER_URL, connect_timeout=15) as conn, conn.cursor() as cur:
        columns = _columns(cur, "propuestas_clinicas")
    assert "estado" in columns
    assert "resumen_propuesto" in columns
    assert "conclusion_medica" in columns

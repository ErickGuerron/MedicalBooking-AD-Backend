"""The catalog read from SQL Server has the same rows as the tables (unit 3)."""

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.replica.infrastructure.supabase_catalog import read_catalog

pytestmark = pytest.mark.integration


def test_the_catalog_matches_the_sql_tables_row_for_row(migrated_engine: Engine) -> None:
    snapshot = read_catalog(migrated_engine)

    with migrated_engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in ("roles", "permisos", "roles_permisos")
        }
    assert len(snapshot.roles) == counts["roles"] > 0
    assert len(snapshot.permisos) == counts["permisos"] > 0
    assert len(snapshot.roles_permisos) == counts["roles_permisos"] > 0


def test_the_own_results_permission_is_in_the_patient_role(migrated_engine: Engine) -> None:
    snapshot = read_catalog(migrated_engine)

    paciente = next(rol_id for rol_id, nombre in snapshot.roles if nombre == "PACIENTE")
    own = next(
        permiso_id
        for permiso_id, codigo, *_ in snapshot.permisos
        if codigo == "resultados.leer_propios"
    )
    assert (paciente, own) in snapshot.roles_permisos

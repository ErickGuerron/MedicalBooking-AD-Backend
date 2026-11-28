"""Watermark user reader against SQL Server: rows above the watermark, with roles and patient."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

from medicalbooking.modules.synchronization.infrastructure.changed_usuarios import (
    SqlChangedUsuariosReader,
)


def _cleanup(engine: Engine, usuario_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pacientes WHERE usuario_id = :u"), {"u": usuario_id})
        conn.execute(text("DELETE FROM usuarios_roles WHERE usuario_id = :u"), {"u": usuario_id})
        conn.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": usuario_id})


def _max_version(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(text("SELECT CAST(MAX(version_fila) AS BIGINT) FROM usuarios")).scalar()
            or 0
        )


def test_a_new_user_appears_with_roles_and_patient_identity(migrated_engine: Engine) -> None:
    reader = SqlChangedUsuariosReader(migrated_engine)
    watermark = _max_version(migrated_engine)
    usuario_id = str(uuid.uuid4())
    paciente_id = str(uuid.uuid4())
    try:
        with migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO usuarios (id, email, nombre, apellido, estado, creado_en, "
                    "actualizado_en) VALUES (:id, :email, 'Ana', 'Lopez', 'ACTIVO', "
                    "SYSUTCDATETIME(), SYSUTCDATETIME())"
                ),
                {"id": usuario_id, "email": f"sync-{usuario_id}@test.local"},
            )
            role_id = conn.execute(text("SELECT TOP 1 id FROM roles ORDER BY id")).scalar()
            conn.execute(
                text("INSERT INTO usuarios_roles (usuario_id, rol_id) VALUES (:u, :r)"),
                {"u": usuario_id, "r": role_id},
            )
            conn.execute(
                text(
                    "INSERT INTO pacientes (id, usuario_id, creado_en) "
                    "VALUES (:p, :u, SYSUTCDATETIME())"
                ),
                {"p": paciente_id, "u": usuario_id},
            )

        changes = reader.changes_since(watermark, limit=500)
        mine = [change for change in changes if change.payload["usuario"]["id"] == usuario_id]

        assert len(mine) == 1
        payload = mine[0].payload
        assert payload["usuario"] == {"id": usuario_id, "estado": "ACTIVO"}
        assert payload["roles"] == [int(role_id)]
        assert payload["paciente"] == {
            "id": paciente_id,
            "usuario_id": usuario_id,
            "nombre": "Ana",
            "apellido": "Lopez",
        }
        assert mine[0].version > watermark
    finally:
        _cleanup(migrated_engine, usuario_id)


def test_rows_at_or_below_the_watermark_are_not_returned(migrated_engine: Engine) -> None:
    reader = SqlChangedUsuariosReader(migrated_engine)
    watermark = _max_version(migrated_engine)

    changes = reader.changes_since(watermark, limit=500)

    assert all(change.version > watermark for change in changes)


def test_the_limit_must_be_positive(migrated_engine: Engine) -> None:
    import pytest

    with pytest.raises(ValueError, match="limit"):
        SqlChangedUsuariosReader(migrated_engine).changes_since(0, limit=0)

"""Read users changed since a watermark, as the same snapshot the replica projection applies.

Writes made outside the API leave no outbox event, so the watermark sync reads the rows directly.
The payload matches the ``usuario.creado`` event built by identity.infrastructure.repositories:
the account state, the role ids and the patient identity with the name the replicas keep. The
replica projection applies it with the same handler as ``usuario.creado``.

The row version is ``usuarios.version_fila`` (rowversion), read as BIGINT. It moves on changes to
the user row only: a role granted or removed in ``usuarios_roles`` does not move it, so such a
change is picked up when the user row changes next (documented limit).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, bindparam, text


@dataclass(frozen=True)
class UsuarioChange:
    version: int
    payload: dict[str, Any]


_CHANGED_USUARIOS = text(
    "SELECT TOP (:limite) CAST(u.version_fila AS BIGINT) AS version, "
    "u.id, u.estado, u.nombre, u.apellido, p.id AS paciente_id "
    "FROM usuarios AS u LEFT JOIN pacientes AS p ON p.usuario_id = u.id "
    "WHERE CAST(u.version_fila AS BIGINT) > :desde "
    "ORDER BY CAST(u.version_fila AS BIGINT) ASC"
)

_ROLES_OF = text(
    "SELECT usuario_id, rol_id FROM usuarios_roles WHERE usuario_id IN :ids"
).bindparams(bindparam("ids", expanding=True))


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the replica keys are lower case."""
    return str(uuid.UUID(str(value)))


def usuario_payload(row: Mapping[str, Any], roles: Sequence[int]) -> dict[str, Any]:
    """Map one user row and its role ids to the ``usuario.creado``-shaped payload."""
    usuario_id = _canonical(row["id"])
    paciente = None
    if row["paciente_id"] is not None:
        paciente = {
            "id": _canonical(row["paciente_id"]),
            "usuario_id": usuario_id,
            "nombre": row["nombre"],
            "apellido": row["apellido"],
        }
    return {
        "usuario": {"id": usuario_id, "estado": row["estado"]},
        "roles": sorted(int(rol) for rol in roles),
        "paciente": paciente,
    }


class SqlChangedUsuariosReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def changes_since(self, watermark: int, limit: int) -> list[UsuarioChange]:
        """Users with a version above ``watermark``, oldest first, at most ``limit`` of them."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._engine.connect() as conn:
            rows = (
                conn.execute(_CHANGED_USUARIOS, {"desde": watermark, "limite": limit})
                .mappings()
                .all()
            )
            ids = [row["id"] for row in rows]
            roles_by_user: dict[str, list[int]] = {}
            if ids:
                for role in conn.execute(_ROLES_OF, {"ids": ids}).mappings().all():
                    roles_by_user.setdefault(_canonical(role["usuario_id"]), []).append(
                        int(role["rol_id"])
                    )
        return [
            UsuarioChange(
                version=int(row["version"]),
                payload=usuario_payload(row, roles_by_user.get(_canonical(row["id"]), [])),
            )
            for row in rows
        ]

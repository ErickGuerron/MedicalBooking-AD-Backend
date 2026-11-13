"""Authorization catalog for the replica: roles, permissions and their links.

The catalog holds no personal data and changes only with migrations, so it is replaced
as a whole inside one transaction. If any insert fails, the commit does not happen and the
replica keeps the previous catalog: it never serves a half-loaded set of permissions.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CatalogSnapshot:
    roles: tuple[tuple[int, str], ...]
    permisos: tuple[tuple[int, str, str, str, str | None], ...]
    roles_permisos: tuple[tuple[int, int], ...]


class _Executor(Protocol):
    def execute(self, sql: str, params: Any = None) -> object: ...


class CatalogTarget(Protocol):
    def transaction(self) -> AbstractContextManager[_Executor]: ...


_DELETE_LINKS = "DELETE FROM replica.roles_permisos"
_DELETE_PERMISOS = "DELETE FROM replica.permisos"
_DELETE_ROLES = "DELETE FROM replica.roles"
_INSERT_ROLE = "INSERT INTO replica.roles (id, nombre) VALUES (%s, %s)"
_INSERT_PERMISO = (
    "INSERT INTO replica.permisos (id, codigo, recurso, accion, descripcion) "
    "VALUES (%s, %s, %s, %s, %s)"
)
_INSERT_LINK = "INSERT INTO replica.roles_permisos (rol_id, permiso_id) VALUES (%s, %s)"


def replace_catalog(snapshot: CatalogSnapshot, target: CatalogTarget) -> None:
    with target.transaction() as conn:
        conn.execute(_DELETE_LINKS)
        conn.execute(_DELETE_PERMISOS)
        conn.execute(_DELETE_ROLES)
        for role_id, nombre in snapshot.roles:
            conn.execute(_INSERT_ROLE, (role_id, nombre))
        for permiso in snapshot.permisos:
            conn.execute(_INSERT_PERMISO, permiso)
        for rol_id, permiso_id in snapshot.roles_permisos:
            conn.execute(_INSERT_LINK, (rol_id, permiso_id))


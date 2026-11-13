"""SQL Server source and Supabase target for the authorization catalog (unit 3).

The source reads only the catalog tables. The target opens one transaction per sync
through the writer role; replace_catalog decides what to write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, text

from medicalbooking.modules.replica.application.catalog_sync import CatalogSnapshot


def read_catalog(engine: Engine) -> CatalogSnapshot:
    with engine.connect() as conn:
        roles = tuple(
            (int(row.id), row.nombre)
            for row in conn.execute(text("SELECT id, nombre FROM roles ORDER BY id")).all()
        )
        permisos = tuple(
            (int(row.id), row.codigo, row.recurso, row.accion, row.descripcion)
            for row in conn.execute(
                text(
                    "SELECT id, codigo, recurso, accion, descripcion FROM permisos ORDER BY id"
                )
            ).all()
        )
        links = tuple(
            (int(row.rol_id), int(row.permiso_id))
            for row in conn.execute(
                text("SELECT rol_id, permiso_id FROM roles_permisos ORDER BY rol_id, permiso_id")
            ).all()
        )
    return CatalogSnapshot(roles=roles, permisos=permisos, roles_permisos=links)


class _CursorExecutor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def execute(self, sql: str, params: Any = None) -> None:
        self._cursor.execute(sql, params)


class SupabaseCatalogTarget:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @contextmanager
    def transaction(self) -> Iterator[_CursorExecutor]:
        connection = self._connect()
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    yield _CursorExecutor(cursor)
        finally:
            connection.close()

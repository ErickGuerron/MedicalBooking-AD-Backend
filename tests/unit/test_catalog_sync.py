from contextlib import contextmanager

import pytest

from medicalbooking.modules.replica.application.catalog_sync import (
    CatalogSnapshot,
    replace_catalog,
)

SNAPSHOT = CatalogSnapshot(
    roles=((1, "PACIENTE"), (2, "MEDICO")),
    permisos=((10, "resultados.leer_propios", "resultados", "leer_propios", "Leer propios"),),
    roles_permisos=((1, 10),),
)


class FakeConnection:
    def __init__(self, fail_on: str | None = None) -> None:
        self.log: list[tuple[str, str]] = []
        self._fail_on = fail_on

    def execute(self, sql: str, params=None) -> None:
        statement = " ".join(sql.split())
        self.log.append(("execute", statement))
        if self._fail_on and statement.startswith(self._fail_on):
            raise RuntimeError("insert rejected")


class FakeTarget:
    def __init__(self, fail_on: str | None = None) -> None:
        self.connection = FakeConnection(fail_on)
        self.committed = False

    @contextmanager
    def transaction(self):
        try:
            yield self.connection
        except Exception:
            self.committed = False
            raise
        else:
            self.committed = True


def test_the_catalog_is_replaced_inside_one_transaction():
    target = FakeTarget()

    replace_catalog(SNAPSHOT, target)

    statements = [s for _, s in target.connection.log]
    assert target.committed
    assert statements[0].startswith("DELETE FROM replica.roles_permisos")
    assert statements.index("DELETE FROM replica.roles_permisos") < statements.index(
        "DELETE FROM replica.permisos"
    )
    assert any(s.startswith("INSERT INTO replica.roles ") for s in statements)
    assert any(s.startswith("INSERT INTO replica.permisos ") for s in statements)
    assert any(s.startswith("INSERT INTO replica.roles_permisos ") for s in statements)


def test_a_failed_insert_does_not_commit_and_keeps_the_previous_catalog():
    target = FakeTarget(fail_on="INSERT INTO replica.permisos")

    with pytest.raises(RuntimeError):
        replace_catalog(SNAPSHOT, target)

    assert target.committed is False


def test_only_catalog_columns_are_written():
    target = FakeTarget()

    replace_catalog(SNAPSHOT, target)

    statements = " ".join(s for _, s in target.connection.log)
    for personal in ("email", "password", "telefono", "usuario"):
        assert personal not in statements.lower()


def test_an_empty_source_clears_the_replica_catalog():
    target = FakeTarget()

    replace_catalog(CatalogSnapshot(roles=(), permisos=(), roles_permisos=()), target)

    statements = [s for _, s in target.connection.log]
    assert statements[:3] == [
        "DELETE FROM replica.roles_permisos",
        "DELETE FROM replica.permisos",
        "DELETE FROM replica.roles",
    ]
    assert target.committed

"""Replica presence listing: the ids each destination holds, read-only, without a database.

The listing feeds hard-delete detection. Ids come back as lower-case text, the same form the
source reads use.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.replica.infrastructure import supabase_presence
from medicalbooking.modules.replica.infrastructure.supabase_presence import SupabasePresenceCheck
from medicalbooking.modules.synchronization.infrastructure import mongo_presence
from medicalbooking.modules.synchronization.infrastructure.mongo_presence import MongoPresenceCheck

USUARIO = "11111111-2222-4333-8444-555555555555"
CITA = "66666666-7777-4888-8999-aaaaaaaaaaaa"


class _Collection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def find(self, query: dict[str, Any], projection: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((query, projection))
        return list(self._documents)


class _Database:
    def __init__(self, collections: dict[str, _Collection]) -> None:
        self._collections = collections
        self.requested: list[str] = []

    def __getitem__(self, name: str) -> _Collection:
        self.requested.append(name)
        return self._collections[name]


def test_mongo_lists_every_id_of_the_usuarios_collection_in_lower_case() -> None:
    usuarios = _Collection([{"_id": USUARIO.upper()}, {"_id": CITA}])
    database = _Database({"usuarios": usuarios})

    ids = MongoPresenceCheck(database).list_ids("usuario")

    assert ids == frozenset({USUARIO, CITA})
    assert database.requested == ["usuarios"]
    assert usuarios.calls == [({}, {"_id": 1})]


def test_mongo_lists_the_citas_collection_for_cita() -> None:
    citas = _Collection([{"_id": CITA}])
    database = _Database({"citas": citas})

    assert MongoPresenceCheck(database).list_ids("cita") == frozenset({CITA})
    assert database.requested == ["citas"]


def test_mongo_listing_of_an_empty_collection_is_empty() -> None:
    database = _Database({"usuarios": _Collection([])})

    assert MongoPresenceCheck(database).list_ids("usuario") == frozenset()


class _Cursor:
    def __init__(self, rows: list[tuple[str]], log: list[tuple[str, Any]]) -> None:
        self._rows = rows
        self._log = log

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> None:
        self._log.append((query, params))

    def fetchall(self) -> list[tuple[str]]:
        return list(self._rows)


class _Connection:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows
        self.queries: list[tuple[str, Any]] = []
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self._rows, self.queries)

    def close(self) -> None:
        self.closed = True


def test_supabase_lists_every_id_of_replica_usuarios_and_closes_the_connection() -> None:
    connection = _Connection([(USUARIO,), (CITA,)])

    ids = SupabasePresenceCheck(lambda: connection).list_ids("usuario")

    assert ids == frozenset({USUARIO, CITA})
    assert connection.closed is True
    query, params = connection.queries[0]
    assert "replica.usuarios" in query
    assert "lower(id::text)" in query
    assert params is None


def test_supabase_lists_the_replica_citas_table_for_cita() -> None:
    connection = _Connection([(CITA,)])

    assert SupabasePresenceCheck(lambda: connection).list_ids("cita") == frozenset({CITA})
    assert "replica.citas" in connection.queries[0][0]


def test_supabase_listing_closes_the_connection_when_the_query_fails() -> None:
    class _FailingConnection(_Connection):
        def cursor(self):
            raise RuntimeError("connection lost")

    connection = _FailingConnection([])

    try:
        SupabasePresenceCheck(lambda: connection).list_ids("cita")
    except RuntimeError:
        pass

    assert connection.closed is True


def test_listing_keeps_the_destination_codes_of_the_existing_checks() -> None:
    assert mongo_presence.DESTINATION_CODE == "MONGODB"
    assert supabase_presence.DESTINATION_CODE == "SUPABASE"

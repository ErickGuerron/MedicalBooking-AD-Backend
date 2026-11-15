"""Hard-delete tombstones: one delete event per orphaned aggregate, idempotent.

The event is written through the shared outbox helper.

The store runs on a fake SQLAlchemy engine; the outbox helper is replaced by a recorder, so these
tests never touch a database. The version arithmetic lives in the shared helper and is covered by
its own tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from medicalbooking.modules.synchronization.infrastructure import reconciliation_repositories
from medicalbooking.modules.synchronization.infrastructure.reconciliation_repositories import (
    SqlReconciliationStore,
)

USUARIO = "11111111-2222-4333-8444-555555555555"
CITA = "66666666-7777-4888-8999-aaaaaaaaaaaa"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
# The outbox helper receives naive UTC, as in the other modules.
NOW_NAIVE = datetime(2026, 11, 14, 12, 0)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Connection:
    def __init__(self, existing_delete: Any = None) -> None:
        self._existing_delete = existing_delete
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, params=None) -> _Result:
        self.statements.append((str(statement), dict(params or {})))
        return _Result(self._existing_delete)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @contextmanager
    def begin(self) -> Iterator[_Connection]:
        yield self.connection


@pytest.fixture
def appended(monkeypatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        reconciliation_repositories,
        "append_outbox",
        lambda *args: calls.append(args),
    )
    return calls


def test_an_orphaned_usuario_gets_one_usuario_eliminado_event_on_the_shared_helper(
    appended,
) -> None:
    connection = _Connection()
    store = SqlReconciliationStore(_Engine(connection))

    assert store.tombstone("usuario", USUARIO, NOW) is True

    assert appended == [
        (
            connection,
            "usuario",
            USUARIO,
            "usuario.eliminado",
            {"usuario": {"id": USUARIO}},
            NOW_NAIVE,
        )
    ]


def test_an_orphaned_cita_gets_one_cita_eliminada_event_on_the_shared_helper(appended) -> None:
    connection = _Connection()
    store = SqlReconciliationStore(_Engine(connection))

    assert store.tombstone("cita", CITA, NOW) is True

    assert appended == [
        (connection, "cita", CITA, "cita.eliminada", {"cita": {"id": CITA}}, NOW_NAIVE)
    ]


def test_an_aggregate_that_already_has_a_delete_event_is_not_tombstoned_again(appended) -> None:
    connection = _Connection(existing_delete=(1,))
    store = SqlReconciliationStore(_Engine(connection))

    assert store.tombstone("usuario", USUARIO, NOW) is False

    assert appended == []


def test_the_existing_delete_probe_locks_the_aggregate_key_range(appended) -> None:
    connection = _Connection()
    store = SqlReconciliationStore(_Engine(connection))

    store.tombstone("cita", CITA, NOW)

    probe_sql, probe_params = connection.statements[0]
    assert "UPDLOCK, HOLDLOCK" in probe_sql
    assert "eventos_outbox" in probe_sql
    assert probe_params["agregado"] == CITA
    assert probe_params["tipo"] == "cita"
    assert {"cita.eliminada", "usuario.eliminado"} <= set(probe_params["eliminados"])


def test_an_uppercase_guid_is_written_in_canonical_lower_case(appended) -> None:
    connection = _Connection()
    store = SqlReconciliationStore(_Engine(connection))

    store.tombstone("usuario", USUARIO.upper(), NOW)

    assert appended[0][2] == USUARIO


def test_a_replica_id_that_is_not_a_guid_is_skipped_without_writing(appended) -> None:
    connection = _Connection()
    store = SqlReconciliationStore(_Engine(connection))

    assert store.tombstone("usuario", "not-a-guid", NOW) is False

    assert appended == []
    assert connection.statements == []


def test_only_tracked_aggregate_types_can_be_tombstoned(appended) -> None:
    store = SqlReconciliationStore(_Engine(_Connection()))

    with pytest.raises(ValueError):
        store.tombstone("orden_laboratorio", USUARIO, NOW)

    assert appended == []

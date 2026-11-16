"""MongoDB projection keys: aggregate ids are stored in canonical lower-case.

SQL Server returns uniqueidentifier text in upper case. The reconciliation compares lower-case
ids, so an upper-case key written to MongoDB would never match and would be repaired forever.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.replica.infrastructure.mongo_projection import MongoProjectionSink

UPPER_ID = "5E5E5E5E-0000-4000-8000-000000000005"
LOWER_ID = "5e5e5e5e-0000-4000-8000-000000000005"


class _RecordingCollection:
    def __init__(self, name: str, calls: list[tuple[str, str, Any]]) -> None:
        self._name = name
        self._calls = calls

    def find_one(self, filter: dict[str, Any], projection: Any = None) -> None:
        return None

    def update_one(self, filter: dict[str, Any], update: Any, upsert: bool = False) -> None:
        self._calls.append((self._name, "update_one", filter))

    def delete_one(self, filter: dict[str, Any]) -> None:
        self._calls.append((self._name, "delete_one", filter))

    def delete_many(self, filter: dict[str, Any]) -> None:
        self._calls.append((self._name, "delete_many", filter))

    def insert_one(self, document: dict[str, Any]) -> None:
        self._calls.append((self._name, "insert_one", document))


class _RecordingDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def __getitem__(self, name: str) -> _RecordingCollection:
        return _RecordingCollection(name, self.calls)


def _filters(calls: list[tuple[str, str, Any]], collection: str) -> list[Any]:
    return [args for name, _, args in calls if name == collection]


def test_an_upper_case_account_is_written_under_its_lower_case_key() -> None:
    database = _RecordingDatabase()

    with MongoProjectionSink(database, version=1).transaction() as tx:
        tx.upsert_account(UPPER_ID, "INACTIVO")

    assert _filters(database.calls, "usuarios") == [{"_id": LOWER_ID, "_v": {"$lte": 1}}]


def test_a_patient_guarded_by_an_upper_case_account_uses_lower_case_ids() -> None:
    database = _RecordingDatabase()

    with MongoProjectionSink(database, version=1).transaction() as tx:
        tx.upsert_patient(id=UPPER_ID, usuario_id=UPPER_ID, nombre="Ana", apellido="Demo")

    assert _filters(database.calls, "pacientes") == [{"_id": LOWER_ID, "_v": {"$lte": 1}}]


def test_a_delete_writes_the_tombstone_and_removes_the_account_under_lower_case_ids() -> None:
    database = _RecordingDatabase()

    with MongoProjectionSink(database, version=2).transaction() as tx:
        tx.delete_account(UPPER_ID)

    assert _filters(database.calls, "tombstones") == [{"_id": LOWER_ID}]
    assert _filters(database.calls, "usuarios") == [{"_id": LOWER_ID}]
    assert _filters(database.calls, "pacientes") == [{"usuario_id": LOWER_ID}]

"""The MongoDB directory: replaced in one transaction, with each appointment's doctor."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from medicalbooking.modules.replica.application.directory_sync import DirectorySnapshot
from medicalbooking.modules.replica.infrastructure.mongo_directory import replace_directory_mongo

SNAPSHOT = DirectorySnapshot(
    especialidades=((1, "Hematologia", True),),
    medicos=(("11111111-1111-1111-1111-111111111111", "Luis", "Perez", True),),
    servicios=(
        (
            "22222222-2222-2222-2222-222222222222",
            1,
            "Hemograma",
            30,
            "25.00",
            "USD",
            True,
            True,
            False,
        ),
    ),
    citas_medico=(
        ("33333333-3333-3333-3333-333333333333", "11111111-1111-1111-1111-111111111111"),
    ),
)


class _Log:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.in_transaction = False


class _Collection:
    def __init__(self, name: str, log: _Log, fail_on: str | None) -> None:
        self._name = name
        self._log = log
        self._fail_on = fail_on

    def _record(self, op: str, *args, **kwargs) -> None:
        if self._fail_on == f"{self._name}.{op}":
            raise RuntimeError("write refused")
        self._log.calls.append((self._name, op, self._log.in_transaction, args, kwargs))

    def delete_many(self, filt, session=None):
        self._record("delete_many", filt, session=session)

    def insert_many(self, docs, session=None):
        self._record("insert_many", docs, session=session)

    def update_one(self, filt, update, session=None):
        self._record("update_one", filt, update, session=session)


class _Database:
    def __init__(self, log: _Log, fail_on: str | None = None) -> None:
        self._log = log
        self._fail_on = fail_on
        self.client = _Client(log)

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(name, self._log, self._fail_on)


class _Session:
    def __init__(self, log: _Log) -> None:
        self._log = log

    @contextmanager
    def start_transaction(self):
        self._log.in_transaction = True
        try:
            yield self
        finally:
            self._log.in_transaction = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Client:
    def __init__(self, log: _Log) -> None:
        self._log = log

    def start_session(self) -> _Session:
        return _Session(self._log)


def _run(fail_on: str | None = None) -> _Log:
    log = _Log()
    replace_directory_mongo(SNAPSHOT, _Database(log, fail_on))
    return log


def test_every_write_runs_inside_the_transaction() -> None:
    log = _run()

    assert log.calls
    assert all(in_tx for _, _, in_tx, _, _ in log.calls)


def test_old_documents_are_deleted_before_the_new_ones_are_inserted() -> None:
    log = _run()

    ops = [(name, op) for name, op, *_ in log.calls]
    last_delete = max(i for i, (_, op) in enumerate(ops) if op == "delete_many")
    first_insert = min(i for i, (_, op) in enumerate(ops) if op == "insert_many")
    assert last_delete < first_insert


def test_the_appointment_doctor_is_set_by_appointment_id() -> None:
    log = _run()

    updates = [call for call in log.calls if call[1] == "update_one"]
    assert len(updates) == 1
    name, _, _, args, _ = updates[0]
    assert name == "citas"
    assert args[0] == {"_id": "33333333-3333-3333-3333-333333333333"}
    assert args[1] == {"$set": {"medico_id": "11111111-1111-1111-1111-111111111111"}}


def test_doctors_carry_name_only_and_no_patient_fields() -> None:
    log = _run()

    doctor = next(
        args[0][0]
        for name, op, _, args, _ in log.calls
        if name == "medicos" and op == "insert_many"
    )
    assert doctor == {
        "_id": "11111111-1111-1111-1111-111111111111",
        "nombre": "Luis",
        "apellido": "Perez",
        "activo": True,
    }


def test_an_empty_list_is_not_inserted() -> None:
    empty = DirectorySnapshot(especialidades=(), medicos=(), servicios=(), citas_medico=())
    log = _Log()

    replace_directory_mongo(empty, _Database(log))

    assert not [call for call in log.calls if call[1] == "insert_many"]


def test_a_refused_write_propagates_so_the_transaction_aborts() -> None:
    with pytest.raises(RuntimeError):
        _run(fail_on="medicos.insert_many")

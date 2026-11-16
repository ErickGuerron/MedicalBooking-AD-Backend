from __future__ import annotations

from contextlib import contextmanager

import pytest

from medicalbooking.modules.replica.application.directory_sync import (
    DirectorySnapshot,
    replace_directory,
)

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


class _Recorder:
    def __init__(self, fail_on: str | None = None) -> None:
        self.statements: list[str] = []
        self._fail_on = fail_on

    def execute(self, sql: str, params=None) -> None:
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("insert refused")
        self.statements.append(sql)


class _Target:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield self.recorder


def test_the_directory_is_replaced_inside_one_transaction() -> None:
    target = _Target(_Recorder())

    replace_directory(SNAPSHOT, target)

    assert target.transactions == 1


def test_old_rows_are_deleted_before_the_new_ones_are_inserted() -> None:
    recorder = _Recorder()

    replace_directory(SNAPSHOT, _Target(recorder))

    deletes = [i for i, sql in enumerate(recorder.statements) if sql.startswith("DELETE")]
    inserts = [i for i, sql in enumerate(recorder.statements) if sql.startswith("INSERT")]
    assert deletes and inserts
    assert max(deletes) < min(inserts)


def test_the_appointment_doctor_is_set_for_each_appointment() -> None:
    recorder = _Recorder()

    replace_directory(SNAPSHOT, _Target(recorder))

    assert any(sql.startswith("UPDATE replica.citas SET medico_id") for sql in recorder.statements)


def test_a_failed_insert_propagates_so_the_transaction_rolls_back() -> None:
    target = _Target(_Recorder(fail_on="replica.medicos"))

    with pytest.raises(RuntimeError):
        replace_directory(SNAPSHOT, target)


def test_directory_ids_are_lowercase_like_the_event_projection() -> None:
    from medicalbooking.modules.replica.infrastructure.supabase_directory import canonical_id

    assert canonical_id("E0B55F1B-0D15-431B-A293-2564C004D0C3") == (
        "e0b55f1b-0d15-431b-a293-2564c004d0c3"
    )

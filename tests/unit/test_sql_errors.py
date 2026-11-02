"""SQL Server error classification: unique conflicts, deadlocks and bounded retry."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from medicalbooking.shared.domain.errors import ServiceUnavailable
from medicalbooking.shared.infrastructure.sql_errors import (
    is_deadlock,
    is_unique_violation,
    retry_on_deadlock,
)


@pytest.mark.parametrize(
    "message",
    [
        "(2627, b\"Violation of UNIQUE KEY constraint 'UQ__citas'. Cannot insert duplicate key\")",
        "(2601, b\"Cannot insert duplicate key row in object 'dbo.citas' with unique index\")",
    ],
)
def test_unique_violations_are_recognised(message: str) -> None:
    assert is_unique_violation(message)


@pytest.mark.parametrize(
    "message",
    [
        '(547, b"The INSERT statement conflicted with the FOREIGN KEY constraint")',
        "(547, b\"The INSERT statement conflicted with the CHECK constraint 'CK_citas'\")",
    ],
)
def test_other_integrity_errors_are_not_treated_as_conflicts(message: str) -> None:
    assert not is_unique_violation(message)


@pytest.mark.parametrize(
    "message",
    [
        "(1205) Transaction was deadlocked on lock resources and has been chosen as the victim",
        "(3960) Snapshot isolation transaction failed in database",
        "[40001] transaction was deadlock victim",
    ],
)
def test_deadlocks_and_snapshot_conflicts_are_recognised(message: str) -> None:
    assert is_deadlock(message)


def test_ordinary_errors_are_not_deadlocks() -> None:
    assert not is_deadlock("(208) Invalid object name 'citas'")


def _deadlock() -> OperationalError:
    return OperationalError("UPDATE x", {}, Exception("(1205) chosen as the deadlock victim"))


def test_retry_succeeds_when_a_later_attempt_works() -> None:
    attempts = []

    def work() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise _deadlock()
        return "done"

    assert retry_on_deadlock(work) == "done"
    assert len(attempts) == 2


def test_persistent_deadlocks_become_a_retryable_503() -> None:
    attempts = []

    def work() -> str:
        attempts.append(1)
        raise _deadlock()

    with pytest.raises(ServiceUnavailable):
        retry_on_deadlock(work)
    assert len(attempts) == 2


def test_non_deadlock_errors_are_not_retried() -> None:
    attempts = []

    def work() -> str:
        attempts.append(1)
        raise IntegrityError("INSERT", {}, Exception("(547) FOREIGN KEY constraint"))

    with pytest.raises(IntegrityError):
        retry_on_deadlock(work)
    assert len(attempts) == 1

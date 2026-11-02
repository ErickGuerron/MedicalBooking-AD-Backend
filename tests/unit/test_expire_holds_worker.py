"""One-shot expiration worker: exit codes and reporting, without a database."""

from __future__ import annotations

from datetime import UTC, datetime

from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.workers.expire_holds import run

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class _Store:
    def __init__(self, expired: int = 0, error: Exception | None = None) -> None:
        self._expired = expired
        self._error = error

    def expire_overdue_holds(self, now, limit):
        if self._error is not None:
            raise self._error
        return self._expired


def _service(store) -> SchedulingService:
    return SchedulingService(store=store, clock=lambda: NOW, hold_minutes=15)


def test_worker_reports_how_many_holds_it_expired(capsys) -> None:
    code = run(_service(_Store(expired=3)))

    assert code == 0
    assert "expired_holds=3" in capsys.readouterr().out


def test_worker_exits_non_zero_and_hides_details_when_the_database_fails(capsys) -> None:
    code = run(_service(_Store(error=RuntimeError("secret connection string"))))

    captured = capsys.readouterr()
    assert code == 1
    assert "expired_holds=" not in captured.out
    assert "secret connection string" not in captured.out + captured.err

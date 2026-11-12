"""Outbox dispatcher worker: exit codes, reporting and handler wiring, without a database."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from medicalbooking.config import Settings
from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.workers import outbox_dispatcher
from medicalbooking.workers.outbox_dispatcher import build_handlers, run

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


class _Store:
    def __init__(self, materialized: int = 0, error: Exception | None = None) -> None:
        self._materialized = materialized
        self._error = error

    def materialize(self) -> int:
        if self._error is not None:
            raise self._error
        return self._materialized

    def claim_due(self, now, lease_until, destination_codes, limit):
        return []

    def complete(self, claim, outcome, now) -> bool:
        return True


def _service(store: _Store) -> OutboxDispatchService:
    return OutboxDispatchService(store=store, handlers=[], clock=lambda: NOW)


def test_worker_reports_the_counts_of_the_cycle(capsys) -> None:
    code = run(_service(_Store(materialized=2)))

    captured = capsys.readouterr()
    assert code == 0
    assert "materialized=2" in captured.out
    assert "delivered=0" in captured.out
    assert "permanent_failures=0" in captured.out


def test_worker_exits_non_zero_and_hides_details_when_the_database_fails(capsys) -> None:
    code = run(_service(_Store(error=RuntimeError("postgres://user:secret@db-host/app"))))

    captured = capsys.readouterr()
    assert code == 1
    assert "materialized=" not in captured.out
    assert "secret" not in captured.out + captured.err


def test_no_handler_is_wired_yet_so_the_worker_only_materializes() -> None:
    assert build_handlers(Settings(sqlserver_database="MedicalBooking")) == []


def test_configuration_errors_exit_non_zero_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def broken_settings() -> Settings:
        raise ValueError("SQLSERVER_PASSWORD=super-secret")

    monkeypatch.setattr(outbox_dispatcher, "Settings", broken_settings)

    code = outbox_dispatcher.main()

    captured = capsys.readouterr()
    assert code == 1
    assert "super-secret" not in captured.out + captured.err


def test_the_module_documents_how_to_run_it() -> None:
    assert "python -m medicalbooking.workers.outbox_dispatcher" in outbox_dispatcher.__doc__


def test_help_prints_the_usage_and_never_touches_the_database(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def must_not_connect() -> Settings:
        raise AssertionError("--help must not read settings or open a database")

    monkeypatch.setattr(outbox_dispatcher, "Settings", must_not_connect)
    monkeypatch.setattr(sys, "argv", ["outbox_dispatcher", "--help"])

    code = outbox_dispatcher.main()

    assert code == 0
    assert "python -m medicalbooking.workers.outbox_dispatcher" in capsys.readouterr().out

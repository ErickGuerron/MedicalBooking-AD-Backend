"""One-shot external-write sync worker: wiring, exit codes and reporting, without a database."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from medicalbooking.workers import sync_external_writes as worker


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def argv(monkeypatch):
    monkeypatch.setattr("sys.argv", ["sync_external_writes"])


def test_help_prints_the_module_docstring_and_exits_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["sync_external_writes", "--help"])

    assert worker.main() == 0
    assert "Run every 15 minutes" in capsys.readouterr().out


def test_reports_how_many_external_writes_were_queued(monkeypatch, argv, capsys) -> None:
    engine = _Engine()
    calls: dict = {}

    def fake_run_cycle(**kwargs):
        calls.update(kwargs)
        return 4

    monkeypatch.setattr(worker, "Settings", lambda: SimpleNamespace())
    monkeypatch.setattr(worker, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(worker, "SqlChangedCitasReader", lambda e: ("reader", e))
    monkeypatch.setattr(worker, "SqlWatermarkStore", lambda e: ("watermarks", e))
    monkeypatch.setattr(worker, "SqlOutboxPublisher", lambda e: ("publisher", e))
    monkeypatch.setattr(worker, "run_cycle", fake_run_cycle)

    assert worker.main() == 0
    assert capsys.readouterr().out.strip() == "queued=4"
    assert calls["table"] == worker.TABLE_CITAS
    assert calls["batch_size"] == worker.BATCH_SIZE
    assert calls["reader"] == ("reader", engine)
    assert calls["watermarks"] == ("watermarks", engine)
    assert calls["publisher"] == ("publisher", engine)
    assert engine.disposed is True


def test_exits_one_when_configuration_cannot_be_loaded(monkeypatch, argv, caplog, capsys) -> None:
    def broken_settings():
        raise ValueError("value 'hunter2' is invalid")

    monkeypatch.setattr(worker, "Settings", broken_settings)

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "ValueError" in caplog.text
    assert "hunter2" not in caplog.text + capsys.readouterr().out


def test_exits_one_and_hides_details_when_the_database_fails(
    monkeypatch, argv, caplog, capsys
) -> None:
    engine = _Engine()

    def failing_run_cycle(**_kwargs):
        raise RuntimeError("Server=secret-host;Pwd=hunter2")

    monkeypatch.setattr(worker, "Settings", lambda: SimpleNamespace())
    monkeypatch.setattr(worker, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(worker, "SqlChangedCitasReader", lambda e: None)
    monkeypatch.setattr(worker, "SqlWatermarkStore", lambda e: None)
    monkeypatch.setattr(worker, "SqlOutboxPublisher", lambda e: None)
    monkeypatch.setattr(worker, "run_cycle", failing_run_cycle)

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "RuntimeError" in caplog.text
    assert "hunter2" not in caplog.text + capsys.readouterr().out
    assert "queued=" not in capsys.readouterr().out
    assert engine.disposed is True


def test_exits_one_when_the_engine_cannot_be_created(monkeypatch, argv, caplog) -> None:
    def failing_engine(_settings):
        raise RuntimeError("cannot open connection")

    monkeypatch.setattr(worker, "Settings", lambda: SimpleNamespace())
    monkeypatch.setattr(worker, "create_database_engine", failing_engine)

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "RuntimeError" in caplog.text

"""Replica reconciliation worker: arguments, optional checks and exit codes, without a database."""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from medicalbooking.workers import reconcile_replicas as worker


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _Engine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _MongoClient:
    instances: list[_MongoClient] = []

    def __init__(self, uri: str, serverSelectionTimeoutMS: int) -> None:
        self.uri = uri
        self.timeout = serverSelectionTimeoutMS
        self.closed = False
        self.database = object()
        _MongoClient.instances.append(self)

    def __getitem__(self, name: str):
        self.requested_database = name
        return self.database

    def close(self) -> None:
        self.closed = True


class _Report:
    def __init__(self, aggregates: int, repairs: dict[str, int]) -> None:
        self.aggregates = aggregates
        self.repairs = repairs


class _FakeReconciler:
    instances: list[_FakeReconciler] = []
    report = _Report(aggregates=0, repairs={})

    def __init__(self, source, targets, repairs) -> None:
        self.targets = targets
        self.since = None
        _FakeReconciler.instances.append(self)

    def run(self, since: datetime) -> _Report:
        self.since = since
        return _FakeReconciler.report


def _settings(**overrides):
    values = {
        "mongodb_projection_uri": None,
        "mongodb_database": None,
        "supabase_database_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def wired(monkeypatch):
    """Replace every database-facing collaborator; return the engine the worker will use."""
    engine = _Engine()
    _MongoClient.instances = []
    _FakeReconciler.instances = []
    _FakeReconciler.report = _Report(aggregates=0, repairs={})
    monkeypatch.setattr(worker, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(worker, "SqlReconciliationStore", lambda e: ("store", e))
    monkeypatch.setattr(worker, "ReplicaReconciler", _FakeReconciler)
    monkeypatch.setattr(worker, "MongoClient", _MongoClient)
    monkeypatch.setattr(worker, "MongoPresenceCheck", lambda db: ("mongo-check", db))
    monkeypatch.setattr(
        worker, "SupabasePresenceCheck", lambda connect: ("supabase-check", connect)
    )
    monkeypatch.setattr("sys.argv", ["reconcile_replicas"])
    return engine


def test_help_prints_the_module_docstring_and_exits_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["reconcile_replicas", "-h"])

    assert worker.main() == 0
    assert "Run every 15 minutes" in capsys.readouterr().out


def test_default_lookback_is_thirty_days() -> None:
    assert worker._parse_days([]) == worker.DEFAULT_DAYS == 30


def test_days_option_sets_the_lookback() -> None:
    assert worker._parse_days(["--days", "7"]) == 7


def test_rejects_a_lookback_below_one_day(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["reconcile_replicas", "--days", "0"])
    monkeypatch.setattr(worker, "Settings", lambda: _settings())

    assert worker.main() == 1
    assert "--days must be at least 1" in capsys.readouterr().err


def test_exits_one_when_configuration_cannot_be_loaded(monkeypatch, wired, caplog) -> None:
    def broken_settings():
        raise ValueError("value 'hunter2' is invalid")

    monkeypatch.setattr(worker, "Settings", broken_settings)

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "ValueError" in caplog.text
    assert "hunter2" not in caplog.text


def test_skips_both_checks_when_their_settings_are_unset(monkeypatch, wired, capsys) -> None:
    _FakeReconciler.report = _Report(aggregates=3, repairs={})
    monkeypatch.setattr(worker, "Settings", lambda: _settings())

    assert worker.main() == 0

    assert capsys.readouterr().out.strip() == (
        "aggregates=3 repairs_mongodb=0 repairs_supabase=0 skipped_supabase=True"
    )
    assert _FakeReconciler.instances[0].targets == []
    assert wired.disposed is True


def test_runs_both_checks_and_reports_the_repairs_of_each(monkeypatch, wired, capsys) -> None:
    _FakeReconciler.report = _Report(
        aggregates=5,
        repairs={worker.MONGO_DESTINATION: 2, worker.SUPABASE_DESTINATION: 1},
    )
    monkeypatch.setattr(
        worker,
        "Settings",
        lambda: _settings(
            mongodb_projection_uri=_Secret("mongodb://user:pw@host"),
            mongodb_database="medilo",
            supabase_database_url=_Secret("postgresql://user:pw@host/db"),
        ),
    )

    assert worker.main() == 0

    assert capsys.readouterr().out.strip() == (
        "aggregates=5 repairs_mongodb=2 repairs_supabase=1 skipped_supabase=False"
    )
    targets = _FakeReconciler.instances[0].targets
    assert [name for name, _ in targets] == ["mongo-check", "supabase-check"]
    client = _MongoClient.instances[0]
    assert client.uri == "mongodb://user:pw@host"
    assert client.timeout == worker.MONGO_TIMEOUT_MS
    assert client.requested_database == "medilo"
    assert client.closed is True


def test_only_the_mongodb_check_runs_when_supabase_is_unset(monkeypatch, wired, capsys) -> None:
    monkeypatch.setattr(
        worker,
        "Settings",
        lambda: _settings(
            mongodb_projection_uri=_Secret("mongodb://host"),
            mongodb_database="medilo",
        ),
    )

    assert worker.main() == 0

    assert "skipped_supabase=True" in capsys.readouterr().out
    assert [name for name, _ in _FakeReconciler.instances[0].targets] == ["mongo-check"]


def test_exits_one_and_hides_details_when_the_reconciliation_fails(
    monkeypatch, wired, caplog, capsys
) -> None:
    class _FailingReconciler(_FakeReconciler):
        def run(self, since):
            raise RuntimeError("postgresql://user:hunter2@host")

    monkeypatch.setattr(worker, "ReplicaReconciler", _FailingReconciler)
    monkeypatch.setattr(worker, "Settings", lambda: _settings())

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "RuntimeError" in caplog.text
    assert "hunter2" not in caplog.text + capsys.readouterr().out
    assert wired.disposed is True


def test_exits_one_when_the_engine_cannot_be_created(monkeypatch, caplog) -> None:
    def failing_engine(_settings):
        raise RuntimeError("cannot open connection")

    monkeypatch.setattr(worker, "create_database_engine", failing_engine)
    monkeypatch.setattr(worker, "Settings", lambda: _settings())
    monkeypatch.setattr("sys.argv", ["reconcile_replicas"])

    with caplog.at_level(logging.ERROR):
        assert worker.main() == 1

    assert "RuntimeError" in caplog.text

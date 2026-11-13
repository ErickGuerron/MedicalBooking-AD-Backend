import sys

from medicalbooking.workers import sync_replica_catalog


def test_help_flag_prints_help_and_never_touches_settings(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise AssertionError("--help must not build settings or connect")

    monkeypatch.setattr(sync_replica_catalog, "Settings", fail)
    monkeypatch.setattr(sys, "argv", ["sync_replica_catalog", "--help"])

    assert sync_replica_catalog.main() == 0
    assert "Copy the authorization catalog" in capsys.readouterr().out


def test_missing_writer_url_fails_without_connecting(monkeypatch, capsys):
    class NoWriter:
        supabase_writer_database_url = None

    monkeypatch.setattr(sync_replica_catalog, "Settings", lambda: NoWriter())
    monkeypatch.setattr(sys, "argv", ["sync_replica_catalog"])

    assert sync_replica_catalog.main() == 1

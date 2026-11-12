import sys

from medicalbooking.workers import send_notifications


def test_help_flag_prints_help_and_never_starts_a_run(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise AssertionError("--help must not build settings or touch the database")

    monkeypatch.setattr(send_notifications, "Settings", fail)
    monkeypatch.setattr(sys, "argv", ["send_notifications", "--help"])
    assert send_notifications.main() == 0
    assert "Send due patient notifications" in capsys.readouterr().out

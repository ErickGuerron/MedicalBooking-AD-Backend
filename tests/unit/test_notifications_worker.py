"""Notification sender worker: exit codes, counts-only output, configuration checks.

Runs without a database.
"""

from __future__ import annotations

from pydantic import SecretStr

from medicalbooking.config import Settings
from medicalbooking.modules.notifications.application.dispatcher import SendReport
from medicalbooking.workers.send_notifications import missing_configuration, run

SECRET = "xkeyn-worker-secret"


class _Dispatcher:
    def __init__(self, report: SendReport | None = None, error: Exception | None = None) -> None:
        self._report = report
        self._error = error

    def send_due(self, limit: int) -> SendReport:
        if self._error is not None:
            raise self._error
        return self._report


def test_worker_reports_the_counts_and_exits_zero(capsys) -> None:
    code = run(_Dispatcher(report=SendReport(attempted=4, sent=3, failed=1)))

    assert code == 0
    assert "notifications attempted=4 sent=3 failed=1" in capsys.readouterr().out


def test_worker_exits_non_zero_and_hides_details_when_the_run_fails(capsys) -> None:
    code = run(_Dispatcher(error=RuntimeError(f"connection string {SECRET}")))

    captured = capsys.readouterr()
    assert code == 1
    assert "notifications attempted=" not in captured.out
    assert SECRET not in captured.out + captured.err


def test_configuration_check_names_the_missing_settings_without_their_values() -> None:
    settings = Settings(_env_file=None, brevo_api_key=None, mail_from=None)

    assert missing_configuration(settings) == ["BREVO_API_KEY", "MAIL_FROM"]


def test_configuration_check_is_empty_when_sender_settings_are_present() -> None:
    settings = Settings(
        _env_file=None,
        brevo_api_key=SecretStr(SECRET),
        mail_from="no-reply@example.com",
    )

    assert missing_configuration(settings) == []

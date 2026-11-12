"""One-shot checkout retry worker: reporting and exit codes, without a database."""

from __future__ import annotations

from dataclasses import dataclass

from medicalbooking.modules.payments.application.billing_service import RetryRun
from medicalbooking.workers.retry_failed_checkouts import run


@dataclass
class _Service:
    run_result: RetryRun | None = None
    error: Exception | None = None

    def retry_due_checkouts(self, limit: int) -> RetryRun:
        if self.error is not None:
            raise self.error
        return self.run_result or RetryRun(attempted=0, succeeded=0, failed=0)


def test_worker_reports_what_it_retried_and_exits_zero(capsys) -> None:
    code = run(_Service(RetryRun(attempted=3, succeeded=2, failed=1)))

    captured = capsys.readouterr()
    assert code == 0
    assert "checkout_retries attempted=3 succeeded=2 failed=1" in captured.out


def test_worker_exits_non_zero_and_hides_details_when_the_run_fails(capsys) -> None:
    code = run(_Service(error=RuntimeError("secret connection string")))

    captured = capsys.readouterr()
    assert code == 1
    assert "checkout_retries" not in captured.out
    assert "secret connection string" not in captured.out + captured.err

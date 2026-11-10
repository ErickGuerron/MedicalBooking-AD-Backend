"""Candidate report: the evidence hint, the warning, and the file it writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from medicalbooking.modules.clinical.application.knowledge_candidates import (
    _evidence_hint,
    build_report,
    write_report,
)


def test_guidelines_and_reviews_rank_above_plain_articles() -> None:
    assert _evidence_hint(("Practice Guideline", "Journal Article")) == "practice guideline"
    assert _evidence_hint(("Systematic Review",)) == "systematic review"
    assert _evidence_hint(("Randomized Controlled Trial",)) == "randomized controlled trial"
    assert _evidence_hint(("Journal Article",)) == "journal article"
    assert _evidence_hint(("Editorial",)) == "other"


def test_the_report_says_it_is_not_knowledge() -> None:
    report = build_report(analytes=[], rules=[], generated_at=datetime(2026, 11, 2, tzinfo=UTC))

    assert "Not knowledge" in report["warning"]


def test_the_file_is_written_once_with_a_utc_stamp(tmp_path) -> None:
    report = build_report(
        analytes=[{"analito_id": "x"}],
        rules=[],
        generated_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
    )

    path = write_report(report, tmp_path / "candidates")

    assert path.name == "candidates-20261102T120000Z.json"
    assert json.loads(path.read_text(encoding="utf-8"))["analytes"][0]["analito_id"] == "x"

"""Candidate report: what the external sources say, for a clinician to review. Writes no database.

The report gathers, per analyte, the LOINC profile, the MedlinePlus topics and the PubMed
candidate studies found by the configured queries; per rule, the ICD-11 entity. A reviewer
reads it and records each reference range in knowledge/references.csv with its PMID. Nothing in
the report is a clinical decision, and nothing in it becomes knowledge by itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medicalbooking.modules.clinical.infrastructure.external_sources import (
    Article,
    IcdEntity,
    LoincProfile,
    Topic,
)


def _article(article: Article) -> dict[str, Any]:
    data = asdict(article)
    data["publication_types"] = list(article.publication_types)
    data["mesh_terms"] = list(article.mesh_terms)
    data["evidence_hint"] = _evidence_hint(article.publication_types)
    return data


def _evidence_hint(publication_types: tuple[str, ...]) -> str:
    """A label to help the reviewer prioritise. It is not a quality judgement by itself."""
    kinds = {item.lower() for item in publication_types}
    for label in (
        "practice guideline",
        "systematic review",
        "meta-analysis",
        "randomized controlled trial",
    ):
        if label in kinds:
            return label
    return "journal article" if "journal article" in kinds else "other"


def build_report(
    *,
    analytes: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "warning": (
            "Candidates for clinical review. Not knowledge. Reference ranges must be taken from a "
            "source a clinician has read and recorded in knowledge/references.csv."
        ),
        "analytes": analytes,
        "rules": rules,
    }


def analyte_entry(
    analito_id: str,
    profile: LoincProfile,
    topics: list[Topic],
    studies: list[Article],
    queries: list[str],
) -> dict[str, Any]:
    return {
        "analito_id": analito_id,
        "loinc": {
            "code": profile.code,
            "display": profile.display,
            "spanish_name": profile.spanish_name,
            "scale": profile.scale,
            "specimen": profile.specimen,
            "example_unit": profile.example_unit,
            "class": profile.loinc_class,
        },
        "medlineplus_topics": [
            {"title": t.title, "url": t.url, "summary": t.summary} for t in topics
        ],
        "pubmed_queries": queries,
        "pubmed_candidates": [_article(a) for a in studies],
    }


def rule_entry(
    rule_codigo: str, term: str | None, code: str | None, entity: IcdEntity | None
) -> dict[str, Any]:
    return {
        "regla_codigo": rule_codigo,
        "icd_search": term,
        "icd11": None
        if entity is None
        else {"code": code, "class": entity.class_kind, "browser_url": entity.browser_url},
    }


def write_report(report: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["generated_at"]).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"candidates-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path

"""Write the candidate report for clinical review. Reads the sources; writes a JSON file only.

    uv run python -m medicalbooking.workers.knowledge_candidates

The report is saved under knowledge/candidates/. It never changes the database and it is not
knowledge: it is material for a reviewer, who records reference ranges in knowledge/references.csv.
Exit 0 on success, 1 when a source fails (no file is written).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medicalbooking.config import Settings
from medicalbooking.modules.clinical.application.knowledge_candidates import (
    analyte_entry,
    build_report,
    rule_entry,
    write_report,
)
from medicalbooking.modules.clinical.infrastructure.external_sources import (
    ExternalSourceError,
    Icd11,
    Loinc,
    MedlinePlus,
    PubMed,
)
from medicalbooking.workers.import_knowledge import _missing_credentials, _secret

DEFAULT_CONFIG = Path("knowledge") / "sources.json"
DEFAULT_OUT = Path("knowledge") / "candidates"
MAX_STUDIES_PER_QUERY = 5


def _collect(config: dict[str, Any], settings: Settings) -> dict[str, Any]:
    loinc = Loinc(settings.loinc_username or "", _secret(settings.loinc_password))
    medline = MedlinePlus()
    pubmed = PubMed()
    icd = Icd11(settings.icd11_client_id or "", _secret(settings.icd11_client_secret))

    analytes = []
    for item in config.get("analytes", []):
        profile = loinc.profile(item["loinc"], item["expected_display"])
        topics = medline.topics(item["loinc"]) if item.get("medlineplus") else []
        queries = list(item.get("pubmed_queries", []))
        studies = []
        for query in queries:
            pmids = pubmed.search(query, retmax=MAX_STUDIES_PER_QUERY)
            studies.extend(pubmed.details(pmids))
        analytes.append(analyte_entry(item["analito_id"], profile, topics, studies, queries))

    rules = []
    for rule in config.get("rules", []):
        term = rule.get("icd_search")
        if term:
            code, _title, entity_id = icd.resolve(term)
            entity = icd.entity(entity_id)
            rules.append(rule_entry(rule["regla_codigo"], term, code, entity))
        else:
            rules.append(rule_entry(rule["regla_codigo"], None, None, None))
    return {"analytes": analytes, "rules": rules}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the candidate report for review.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings = Settings()
    missing = _missing_credentials(config, settings)
    if missing:
        print("report not started: missing credentials: " + ", ".join(missing))
        return 1
    try:
        collected = _collect(config, settings)
    except ExternalSourceError as error:
        print(f"report aborted, nothing written: {error.source} {error.kind}")
        return 1

    report = build_report(
        analytes=collected["analytes"],
        rules=collected["rules"],
        generated_at=datetime.now(UTC),
    )
    path = write_report(report, args.out)
    print(f"candidate report written: {path}")
    print("this file is not knowledge: a reviewer records ranges in knowledge/references.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

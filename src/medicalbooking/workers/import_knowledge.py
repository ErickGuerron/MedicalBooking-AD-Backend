"""Import external knowledge sources as drafts, all or nothing.

    uv run python -m medicalbooking.workers.import_knowledge --dry-run
    uv run python -m medicalbooking.workers.import_knowledge

Step 1 fetches everything (no writes). Step 2 writes it in one transaction. A failure in either
step leaves the database exactly as it was. Exit 0 on success, 1 on any failure. Only
the source name and the failure kind are printed, never a response body or a secret.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from medicalbooking.config import Settings
from medicalbooking.modules.clinical.application.knowledge_import import build_plan
from medicalbooking.modules.clinical.infrastructure.external_sources import (
    ExternalSourceError,
    Icd11,
    Loinc,
    MedlinePlus,
    PubMed,
)
from medicalbooking.modules.clinical.infrastructure.knowledge_import_repository import (
    ImportFailed,
    apply_plan,
)
from medicalbooking.shared.infrastructure.database import create_database_engine

DEFAULT_CONFIG = Path("knowledge") / "sources.json"


def _missing_credentials(config: dict[str, Any], settings: Settings) -> list[str]:
    missing: list[str] = []
    if config.get("analytes"):
        if not settings.loinc_username:
            missing.append("LOINC_USERNAME")
        if not settings.loinc_password:
            missing.append("LOINC_PASSWORD")
    if any(rule.get("icd_search") for rule in config.get("rules", [])):
        if not settings.icd11_client_id:
            missing.append("ICD11_CLIENT_ID")
        if not settings.icd11_client_secret:
            missing.append("ICD11_CLIENT_SECRET")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import external knowledge sources as drafts.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings = Settings()
    missing = _missing_credentials(config, settings)
    if missing:
        print("import not started: missing credentials: " + ", ".join(missing))
        return 1

    try:
        plan = build_plan(
            config,
            loinc=Loinc(settings.loinc_username or "", _secret(settings.loinc_password)),
            medlineplus=MedlinePlus(),
            pubmed=PubMed(),
            icd11=Icd11(
                settings.icd11_client_id or "",
                _secret(settings.icd11_client_secret),
            ),
        )
    except ExternalSourceError as error:
        print(f"import aborted before any write: {error.source} {error.kind}")
        return 1

    print(
        f"plan: {len(plan.analyte_codes)} LOINC codes, {len(plan.contents)} educational topics, "
        f"{len(plan.sources)} sources, {len(plan.rule_sources)} rule links, "
        f"{len(plan.diagnoses)} ICD-11 codes"
    )
    for note in plan.notes:
        print(f"  - {note}")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    engine = create_database_engine(settings)
    try:
        counts = apply_plan(engine, plan)
    except ImportFailed as error:
        print(f"import rolled back, nothing kept: {error}")
        return 1
    except Exception as error:  # any database failure: the transaction is already rolled back
        print(f"import rolled back, nothing kept: {type(error).__name__}")
        return 1
    finally:
        engine.dispose()
    print("import committed: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


def _secret(value: Any) -> str:
    return "" if value is None else value.get_secret_value()


if __name__ == "__main__":
    sys.exit(main())

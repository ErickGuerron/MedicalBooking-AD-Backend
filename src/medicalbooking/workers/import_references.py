"""Import reviewed reference ranges from knowledge/references.csv as drafts, all or nothing.

    uv run python -m medicalbooking.workers.import_references --dry-run
    uv run python -m medicalbooking.workers.import_references

Every row cites a PMID. The file is refused as a whole if any row is invalid, and every PMID
must exist in PubMed. The write is one transaction. Exit 0 on success, 1 on any failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from medicalbooking.config import Settings
from medicalbooking.modules.clinical.application.reference_csv import parse_references
from medicalbooking.modules.clinical.infrastructure.external_sources import (
    ExternalSourceError,
    PubMed,
)
from medicalbooking.modules.clinical.infrastructure.knowledge_import_repository import (
    ImportFailed,
    apply_reference_batch,
)
from medicalbooking.shared.infrastructure.database import create_database_engine

DEFAULT_FILE = Path("knowledge") / "references.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import reviewed reference ranges as drafts.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument(
        "--dry-run", action="store_true", help="validate and check PMIDs; write nothing"
    )
    args = parser.parse_args(argv)

    parsed = parse_references(args.file.read_text(encoding="utf-8"))
    if parsed.errors:
        print(f"references not imported: {len(parsed.errors)} problem(s), nothing written")
        for error in parsed.errors:
            print(f"  - {error}")
        return 1

    pmids = sorted({row.pmid for row in parsed.rows})
    try:
        articles = {article.pmid: article for article in PubMed().details(pmids)}
    except ExternalSourceError as error:
        print(f"references not imported: PubMed {error.kind}, nothing written")
        return 1

    print(f"valid: {len(parsed.rows)} reference(s) citing {len(pmids)} PMID(s)")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    engine = create_database_engine(Settings())
    try:
        written = apply_reference_batch(engine, parsed.rows, articles)
    except ImportFailed as error:
        print(f"references rolled back, nothing kept: {error}")
        return 1
    except Exception as error:  # the transaction is already rolled back
        print(f"references rolled back, nothing kept: {type(error).__name__}")
        return 1
    finally:
        engine.dispose()
    print(f"references committed as drafts: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

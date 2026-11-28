"""The clinical knowledge bundle as files in the repository: one JSON Lines file per table.

``manifest.json`` lists every file with its SHA-256 and row count, plus the bundle version and its
digest. Reading verifies each hash, parses the rows, and recomputes the digest from them: a file
edited by hand, a missing row or a changed manifest is refused, never applied silently.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from medicalbooking.modules.clinical.domain.knowledge_digest import (
    TABLES,
    canonical_lines,
    knowledge_digest,
)

MANIFEST_NAME = "manifest.json"


class BundleError(ValueError):
    """The bundle on disk is not the one its manifest describes."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_bundle(
    directory: Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    bundle_version: str,
) -> dict[str, Any]:
    """Write one file per table and the manifest; return the manifest written."""
    directory.mkdir(parents=True, exist_ok=True)
    result = knowledge_digest(tables)
    files: dict[str, dict[str, Any]] = {}
    for spec in TABLES:
        lines = canonical_lines(spec, tables[spec.name])
        content = ("\n".join(lines) + "\n" if lines else "").encode("utf-8")
        (directory / f"{spec.name}.jsonl").write_bytes(content)
        files[spec.name] = {"sha256": _sha256(content), "rows": len(lines)}
    manifest: dict[str, Any] = {
        "bundle_version": bundle_version,
        "digest": result.digest,
        "files": files,
    }
    (directory / MANIFEST_NAME).write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    )
    return manifest


def read_bundle(directory: Path) -> tuple[str, str, dict[str, list[dict[str, Any]]]]:
    """Verify the bundle and return ``(bundle_version, digest, tables)``.

    Raises BundleError on a missing or altered file, a row count that differs from the manifest, or
    a digest recomputed from the rows that differs from the manifest.
    """
    manifest_path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundleError(f"manifest unreadable: {type(error).__name__}") from error

    tables: dict[str, list[dict[str, Any]]] = {}
    for spec in TABLES:
        entry = manifest["files"].get(spec.name)
        if entry is None:
            raise BundleError(f"manifest has no entry for table {spec.name}")
        path = directory / f"{spec.name}.jsonl"
        try:
            content = path.read_bytes()
        except OSError as error:
            raise BundleError(f"bundle file missing: {spec.name}.jsonl") from error
        if _sha256(content) != entry["sha256"]:
            raise BundleError(f"bundle file altered after export: {spec.name}.jsonl")
        rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line]
        if len(rows) != entry["rows"]:
            raise BundleError(f"row count of {spec.name}.jsonl differs from the manifest")
        tables[spec.name] = rows

    digest = knowledge_digest(tables).digest
    if digest != manifest["digest"]:
        raise BundleError("recomputed digest differs from the manifest")
    return str(manifest["bundle_version"]), digest, tables

"""The knowledge bundle on disk: round trip, refusal of altered files, and the committed bundle."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from medicalbooking.modules.clinical.domain.knowledge_bundle import BUNDLE_DIGEST, BUNDLE_VERSION
from medicalbooking.modules.clinical.domain.knowledge_digest import TABLES
from medicalbooking.modules.clinical.infrastructure.knowledge_bundle_files import (
    MANIFEST_NAME,
    BundleError,
    read_bundle,
    write_bundle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tables() -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {spec.name: [] for spec in TABLES}
    tables["fuentes"] = [
        {
            "codigo": "WHO-FERRITIN-2020",
            "nombre": "WHO 2020",
            "organizacion": "OMS",
            "url": None,
            "identificador_publicacion": None,
            "version_publicacion": "2020",
            "fecha_publicacion": date(2020, 4, 1),
            "fecha_consulta": None,
            "licencia": None,
            "vigente": True,
        }
    ]
    tables["analitos"] = [
        {
            "codigo_loinc": "718-7",
            "nombre": "Hemoglobina",
            "descripcion": None,
            "unidad_canonica": "g/dL",
            "tipo_valor": "NUMERICO",
            "activo": True,
        }
    ]
    tables["referencias"] = [
        {
            "analito": "718-7",
            "fuente": "WHO-FERRITIN-2020",
            "version": 1,
            "unidad": "g/dL",
            "sexo_aplicable": None,
            "edad_min_dias": None,
            "edad_max_dias": None,
            "tipo_muestra": None,
            "metodo_analitico": None,
            "contexto": None,
            "referencia_min": Decimal("13.0"),
            "referencia_max": Decimal("17.5"),
            "valor_texto_esperado": None,
            "fecha_inicio": date(2020, 1, 1),
            "fecha_fin": None,
            "estado": "VALIDADA",
        }
    ]
    return tables


def test_write_then_read_returns_the_same_knowledge(tmp_path: Path) -> None:
    written = write_bundle(tmp_path, _tables(), "1.2.3")

    version, digest, tables = read_bundle(tmp_path)

    assert version == "1.2.3"
    assert digest == written["digest"]
    assert tables["referencias"][0]["referencia_min"] == "13"
    assert tables["fuentes"][0]["fecha_publicacion"] == "2020-04-01"


def test_one_file_per_table_plus_manifest(tmp_path: Path) -> None:
    write_bundle(tmp_path, _tables(), "1.0.0")

    names = {path.name for path in tmp_path.iterdir()}
    assert names == {f"{spec.name}.jsonl" for spec in TABLES} | {MANIFEST_NAME}


def test_an_edited_file_is_refused(tmp_path: Path) -> None:
    write_bundle(tmp_path, _tables(), "1.0.0")
    path = tmp_path / "referencias.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("13", "12", 1), encoding="utf-8")

    with pytest.raises(BundleError, match="altered"):
        read_bundle(tmp_path)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    write_bundle(tmp_path, _tables(), "1.0.0")
    (tmp_path / "contenidos.jsonl").unlink()

    with pytest.raises(BundleError, match="missing"):
        read_bundle(tmp_path)


def test_a_manifest_with_another_digest_is_refused(tmp_path: Path) -> None:
    write_bundle(tmp_path, _tables(), "1.0.0")
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["digest"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleError, match="digest differs"):
        read_bundle(tmp_path)


def test_an_unreadable_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="manifest unreadable"):
        read_bundle(tmp_path)


def test_the_committed_bundle_matches_the_generated_constant() -> None:
    version, digest, _ = read_bundle(REPO_ROOT / "knowledge" / "bundle")

    assert version == BUNDLE_VERSION
    assert digest == BUNDLE_DIGEST

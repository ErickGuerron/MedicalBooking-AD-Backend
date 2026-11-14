"""Migration 0029 (treatments): revision chain, new tables only, append-only follow-ups.

The script runs against SQL Server only in the integration suite; here the contract is pinned
statically: one linear step after 0027, no permission seed, and a trigger that refuses changes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def _load(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(filename, VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0029_follows_the_current_head_in_one_linear_step() -> None:
    previous = _load("0028_sincronizacion_watermarks.py")
    current = _load("0029_treatments.py")

    assert current.revision == "0029_treatments"
    assert current.down_revision == previous.revision


def test_upgrade_creates_the_two_treatment_tables_and_no_other_table_change() -> None:
    script = _load("0029_treatments.py")._UPGRADE

    assert "CREATE TABLE [tratamientos]" in script
    assert "CREATE TABLE [tratamientos_seguimientos]" in script
    assert script.count("ALTER TABLE") == 0
    assert "DROP" not in script


def test_upgrade_seeds_no_permission_because_existing_ones_fit() -> None:
    script = _load("0029_treatments.py")._UPGRADE

    assert "permisos" not in script
    assert "roles_permisos" not in script


def test_follow_ups_are_append_only_at_the_database_level() -> None:
    script = _load("0029_treatments.py")._UPGRADE

    assert "TR_tratamientos_seguimientos_inmutables" in script
    assert "AFTER UPDATE, DELETE" in script
    assert "append-only" in script


def test_follow_ups_keep_insertion_order_with_an_identity_sequence() -> None:
    script = _load("0029_treatments.py")._UPGRADE

    assert "[secuencia] bigint IDENTITY(1, 1)" in script


def test_downgrade_drops_only_the_new_tables() -> None:
    module = _load("0029_treatments.py")

    assert "DROP TABLE [tratamientos_seguimientos]" in module._DOWNGRADE
    assert "DROP TABLE [tratamientos];" in module._DOWNGRADE

"""Watermark store against SQL Server: default zero, monotonic advance, one row per table."""

from __future__ import annotations

from sqlalchemy import Engine, text

from medicalbooking.modules.synchronization.infrastructure.watermark_store import (
    SqlWatermarkStore,
)


def _clear(engine: Engine, table: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sincronizacion_watermarks WHERE tabla = :t"), {"t": table})


def test_unknown_table_starts_at_zero(migrated_engine: Engine) -> None:
    store = SqlWatermarkStore(migrated_engine)
    _clear(migrated_engine, "wm_test_unknown")

    assert store.get("wm_test_unknown") == 0


def test_advance_raises_the_watermark_and_never_lowers_it(migrated_engine: Engine) -> None:
    store = SqlWatermarkStore(migrated_engine)
    _clear(migrated_engine, "wm_test_monotonic")

    store.advance("wm_test_monotonic", 40)
    store.advance("wm_test_monotonic", 25)
    store.advance("wm_test_monotonic", 40)

    assert store.get("wm_test_monotonic") == 40


def test_each_table_has_its_own_row(migrated_engine: Engine) -> None:
    store = SqlWatermarkStore(migrated_engine)
    _clear(migrated_engine, "wm_test_a")
    _clear(migrated_engine, "wm_test_b")

    store.advance("wm_test_a", 7)
    store.advance("wm_test_b", 99)

    assert store.get("wm_test_a") == 7
    assert store.get("wm_test_b") == 99

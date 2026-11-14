"""Watermark store input validation that needs no database."""

from __future__ import annotations

import pytest

from medicalbooking.modules.synchronization.infrastructure.watermark_store import (
    SqlWatermarkStore,
)


def test_negative_watermark_is_rejected_before_any_sql_runs() -> None:
    store = SqlWatermarkStore(engine=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="non-negative"):
        store.advance("usuarios", -1)

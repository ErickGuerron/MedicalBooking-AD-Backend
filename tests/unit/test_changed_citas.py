"""Watermark cita reader: snapshot mapping matches the replica projection, no database needed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from medicalbooking.modules.replica.application.projection_consumer import _CITA_FIELDS
from medicalbooking.modules.synchronization.infrastructure.changed_citas import (
    SqlChangedCitasReader,
    cita_snapshot,
)

_CITA_ID = "9B1E2C4A-0000-4000-8000-000000000001"


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": _CITA_ID,
        "paciente_id": "9B1E2C4A-0000-4000-8000-000000000002",
        "servicio_id": "9B1E2C4A-0000-4000-8000-000000000003",
        "estado": "RESERVADA",
        "reservada_en": datetime(2026, 11, 14, 10, 0, tzinfo=UTC).replace(tzinfo=None),
        "confirmada_en": None,
        "cancelada_en": None,
        "motivo_cancelacion_codigo": None,
        "motivo_cancelacion_texto": None,
        "inicio_utc": datetime(2026, 11, 20, 13, 0),
        "fin_utc": datetime(2026, 11, 20, 13, 30),
    }
    row.update(overrides)
    return row


def test_snapshot_has_exactly_the_replica_columns() -> None:
    snapshot = cita_snapshot(_row())

    assert set(snapshot) == set(_CITA_FIELDS)


def test_guids_are_lower_case_and_times_are_isoformat() -> None:
    snapshot = cita_snapshot(_row())

    assert snapshot["id"] == _CITA_ID.lower()
    assert snapshot["inicio"] == "2026-11-20T13:00:00"
    assert snapshot["confirmada_en"] is None


def test_non_positive_limit_is_rejected_before_any_sql_runs() -> None:
    reader = SqlChangedCitasReader(engine=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="positive"):
        reader.changes_since(0, 0)

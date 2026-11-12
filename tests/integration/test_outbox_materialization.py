"""Outbox materialization: one delivery row per outbox event and active destination (S5)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.modules.synchronization.infrastructure.repositories import SqlOutboxStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def aggregate(migrated_engine: Engine) -> Iterator[str]:
    """An aggregate type private to one test, so its events never mix with other tests."""
    aggregate_type = f"TEST_{uuid.uuid4().hex[:12]}"
    yield aggregate_type
    _delete_events(migrated_engine, aggregate_type)


@pytest.fixture
def destination(migrated_engine: Engine) -> Iterator[int]:
    """An active destination without a handler; removed after the test."""
    with migrated_engine.begin() as conn:
        destination_id = _insert_destination(conn, active=1)
    yield destination_id
    _delete_destination(migrated_engine, destination_id)


@pytest.fixture
def inactive_destination(migrated_engine: Engine) -> Iterator[int]:
    with migrated_engine.begin() as conn:
        destination_id = _insert_destination(conn, active=0)
    yield destination_id
    _delete_destination(migrated_engine, destination_id)


def _insert_destination(conn, active: int) -> int:
    code = f"TEST_{uuid.uuid4().hex[:20]}"
    row = conn.execute(
        text(
            "INSERT INTO destinos_sincronizacion (codigo, activo) "
            "OUTPUT INSERTED.id VALUES (:codigo, :activo)"
        ),
        {"codigo": code, "activo": active},
    ).one()
    return int(row[0])


def _delete_destination(engine: Engine, destination_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM entregas_eventos WHERE destino_id = :id"), {"id": destination_id}
        )
        conn.execute(
            text("DELETE FROM destinos_sincronizacion WHERE id = :id"), {"id": destination_id}
        )


def _delete_events(engine: Engine, aggregate_type: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM entregas_eventos WHERE evento_id IN "
                "(SELECT id FROM eventos_outbox WHERE agregado_tipo = :tipo)"
            ),
            {"tipo": aggregate_type},
        )
        conn.execute(
            text("DELETE FROM eventos_outbox WHERE agregado_tipo = :tipo"),
            {"tipo": aggregate_type},
        )


def _seed_events(engine: Engine, aggregate_type: str, count: int) -> None:
    aggregate_id = str(uuid.uuid4())
    with engine.begin() as conn:
        for version in range(1, count + 1):
            conn.execute(
                text(
                    "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, "
                    "payload, version_agregado, creado_en) "
                    "VALUES (:id, :tipo, :agregado, 'prueba', '{}', :version, :ahora)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tipo": aggregate_type,
                    "agregado": aggregate_id,
                    "version": version,
                    "ahora": NOW.replace(tzinfo=None),
                },
            )


def _deliveries(engine: Engine, aggregate_type: str) -> list:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT d.codigo, en.estado, en.intentos, en.destino_id "
                "FROM entregas_eventos AS en "
                "JOIN eventos_outbox AS ev ON ev.id = en.evento_id "
                "JOIN destinos_sincronizacion AS d ON d.id = en.destino_id "
                "WHERE ev.agregado_tipo = :tipo"
            ),
            {"tipo": aggregate_type},
        ).all()


def _active_destinations(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM destinos_sincronizacion WHERE activo = 1")
        ).scalar_one()


def _service(engine: Engine) -> OutboxDispatchService:
    return OutboxDispatchService(store=SqlOutboxStore(engine))


def test_materialize_creates_one_pending_delivery_per_event_and_active_destination(
    migrated_engine: Engine, aggregate: str, destination: int
) -> None:
    _seed_events(migrated_engine, aggregate, count=3)

    _service(migrated_engine).materialize()

    rows = _deliveries(migrated_engine, aggregate)
    assert len(rows) == 3 * _active_destinations(migrated_engine)
    assert {row.estado for row in rows} == {"PENDIENTE"}


def test_materialize_twice_creates_no_duplicate_deliveries(
    migrated_engine: Engine, aggregate: str, destination: int
) -> None:
    _seed_events(migrated_engine, aggregate, count=2)
    service = _service(migrated_engine)
    service.materialize()
    before = _deliveries(migrated_engine, aggregate)

    second_run = service.materialize()

    assert second_run == 0
    assert _deliveries(migrated_engine, aggregate) == before


def test_supabase_and_mongodb_deliveries_stay_pending_without_attempts(
    migrated_engine: Engine, aggregate: str
) -> None:
    _seed_events(migrated_engine, aggregate, count=1)

    _service(migrated_engine).materialize()

    rows = {row.codigo: row for row in _deliveries(migrated_engine, aggregate)}
    assert {"SUPABASE", "MONGODB"} <= rows.keys()
    for code in ("SUPABASE", "MONGODB"):
        assert rows[code].estado == "PENDIENTE"
        assert rows[code].intentos == 0


def test_an_inactive_destination_gets_no_delivery_while_active_ones_do(
    migrated_engine: Engine, aggregate: str, destination: int, inactive_destination: int
) -> None:
    _seed_events(migrated_engine, aggregate, count=2)

    _service(migrated_engine).materialize()

    rows = _deliveries(migrated_engine, aggregate)
    destinations = {row.destino_id for row in rows}
    assert destination in destinations
    assert inactive_destination not in destinations

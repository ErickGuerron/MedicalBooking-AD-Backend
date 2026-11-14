"""Replica reconciliation against SQL Server and MongoDB (phase 1: usuario and cita).

Needs the migrated SQL test database and a MongoDB server in MONGO_TEST_URI (for example
mongodb://127.0.0.1:27018). Tests that need MongoDB are skipped without MONGO_TEST_URI, never
passed. The offline tests at the end use a fake presence check and need only SQL Server. Each test
uses its own UUID aggregates and removes the rows it created.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pymongo import MongoClient
from sqlalchemy import Engine, text

from medicalbooking.modules.synchronization.application.reconciliation import ReplicaReconciler
from medicalbooking.modules.synchronization.infrastructure.mongo_presence import MongoPresenceCheck
from medicalbooking.modules.synchronization.infrastructure.reconciliation_repositories import (
    SqlReconciliationStore,
)

pytestmark = pytest.mark.integration

MONGO_URI = os.environ.get("MONGO_TEST_URI")
LOOKBACK = timedelta(days=30)


@pytest.fixture
def mongo_db() -> Iterator:
    if not MONGO_URI:
        pytest.skip("MONGO_TEST_URI is not set")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    name = f"mb_reconcile_test_{uuid.uuid4().hex[:12]}"
    yield client[name]
    client.drop_database(name)
    client.close()


@pytest.fixture
def created_events(migrated_engine: Engine) -> Iterator[list[str]]:
    """Event ids created by a test; their deliveries and events are removed afterwards."""
    event_ids: list[str] = []
    yield event_ids
    with migrated_engine.begin() as conn:
        for event_id in event_ids:
            conn.execute(text("DELETE FROM entregas_eventos WHERE evento_id = :e"), {"e": event_id})
            conn.execute(text("DELETE FROM eventos_outbox WHERE id = :e"), {"e": event_id})


def _insert_event(
    engine: Engine,
    created_events: list[str],
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    created_at: datetime,
    version: int = 1,
) -> str:
    event_id = str(uuid.uuid4())
    created_events.append(event_id)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, "
                "payload, version_agregado, creado_en) "
                "VALUES (:id, :tipo, :agregado, :evento, '{}', :version, :creado)"
            ),
            {
                "id": event_id,
                "tipo": aggregate_type,
                "agregado": aggregate_id,
                "evento": event_type,
                "version": version,
                "creado": created_at.astimezone(UTC).replace(tzinfo=None),
            },
        )
    return event_id


def _set_delivery(engine: Engine, event_id: str, estado: str) -> None:
    """Create or replace the MONGODB delivery of an event in the given state."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM entregas_eventos WHERE evento_id = :e AND destino_id = "
                "(SELECT id FROM destinos_sincronizacion WHERE codigo = 'MONGODB')"
            ),
            {"e": event_id},
        )
        conn.execute(
            text(
                "INSERT INTO entregas_eventos (id, evento_id, destino_id, estado, intentos, "
                "entregado_en) VALUES (NEWID(), :e, "
                "(SELECT id FROM destinos_sincronizacion WHERE codigo = 'MONGODB'), :estado, 0, "
                "CASE WHEN :estado = 'ENTREGADO' THEN SYSUTCDATETIME() END)"
            ),
            {"e": event_id, "estado": estado},
        )


def _delivery(engine: Engine, event_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT en.estado, en.intentos, en.proximo_intento, en.bloqueado_hasta, "
                "en.ultimo_error, en.entregado_en FROM entregas_eventos AS en "
                "JOIN destinos_sincronizacion AS d ON d.id = en.destino_id "
                "WHERE en.evento_id = :e AND d.codigo = 'MONGODB'"
            ),
            {"e": event_id},
        ).first()
    return None if row is None else dict(row._mapping)


def _reconcile(engine: Engine, mongo_db) -> None:
    ReplicaReconciler(
        source=SqlReconciliationStore(engine),
        targets=[MongoPresenceCheck(mongo_db)],
        repairs=SqlReconciliationStore(engine),
    ).run(since=datetime.now(UTC) - LOOKBACK)


def test_missing_document_resets_an_entregado_delivery_and_is_idempotent(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    user_id = str(uuid.uuid4())
    event_id = _insert_event(
        migrated_engine, created_events, "usuario", user_id, "usuario.creado", datetime.now(UTC)
    )
    _set_delivery(migrated_engine, event_id, "ENTREGADO")

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id) == {
        "estado": "PENDIENTE",
        "intentos": 0,
        "proximo_intento": None,
        "bloqueado_hasta": None,
        "ultimo_error": None,
        "entregado_en": None,
    }

    # The dispatcher has not run yet: the row is PENDIENTE with retry state. A second run must
    # not requeue or touch it.
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE entregas_eventos SET intentos = 2, ultimo_error = 'retry' "
                "WHERE evento_id = :e"
            ),
            {"e": event_id},
        )
    before = _delivery(migrated_engine, event_id)

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id) == before


def test_missing_delivery_row_is_inserted_as_pending(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    event_id = _insert_event(
        migrated_engine,
        created_events,
        "cita",
        str(uuid.uuid4()),
        "cita.actualizada",
        datetime.now(UTC),
    )

    _reconcile(migrated_engine, mongo_db)

    row = _delivery(migrated_engine, event_id)
    assert row is not None
    assert row["estado"] == "PENDIENTE"
    assert row["intentos"] == 0


def test_delete_event_whose_document_still_exists_is_requeued(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    user_id = str(uuid.uuid4())
    mongo_db["usuarios"].insert_one({"_id": user_id, "estado": "ACTIVO", "_v": 1})
    event_id = _insert_event(
        migrated_engine,
        created_events,
        "usuario",
        user_id,
        "usuario.eliminado",
        datetime.now(UTC),
        version=2,
    )
    _set_delivery(migrated_engine, event_id, "ENTREGADO")

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id)["estado"] == "PENDIENTE"


def test_existing_document_needs_no_repair(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    cita_id = str(uuid.uuid4())
    mongo_db["citas"].insert_one({"_id": cita_id, "estado": "CONFIRMADA", "_v": 1})
    event_id = _insert_event(
        migrated_engine,
        created_events,
        "cita",
        cita_id,
        "cita.actualizada",
        datetime.now(UTC),
    )
    _set_delivery(migrated_engine, event_id, "ENTREGADO")

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id)["estado"] == "ENTREGADO"


def test_dispatcher_owned_error_states_are_never_touched(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    user_id = str(uuid.uuid4())
    event_id = _insert_event(
        migrated_engine, created_events, "usuario", user_id, "usuario.creado", datetime.now(UTC)
    )
    _set_delivery(migrated_engine, event_id, "ERROR_PERMANENTE")

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id)["estado"] == "ERROR_PERMANENTE"


def test_latest_event_outside_the_lookback_window_is_not_checked(
    migrated_engine: Engine, mongo_db, created_events: list[str]
) -> None:
    user_id = str(uuid.uuid4())
    event_id = _insert_event(
        migrated_engine,
        created_events,
        "usuario",
        user_id,
        "usuario.creado",
        datetime.now(UTC) - timedelta(days=40),
    )
    _set_delivery(migrated_engine, event_id, "ENTREGADO")

    _reconcile(migrated_engine, mongo_db)

    assert _delivery(migrated_engine, event_id)["estado"] == "ENTREGADO"


class _FakePresence:
    """Stands in for MongoDB: reports the given ids as present, for offline runs only."""

    destination_code = "MONGODB"

    def __init__(self, present: frozenset[str] = frozenset()) -> None:
        self._present = present

    def present_ids(self, aggregate_type: str, ids: list[str]) -> set[str]:
        return {aggregate_id for aggregate_id in ids if aggregate_id in self._present}


def _run_offline(engine: Engine) -> None:
    ReplicaReconciler(
        source=SqlReconciliationStore(engine),
        targets=[_FakePresence()],
        repairs=SqlReconciliationStore(engine),
    ).run(since=datetime.now(UTC) - LOOKBACK)


def test_offline_missing_document_requeues_entregado_and_inserts_missing_row(
    migrated_engine: Engine, created_events: list[str]
) -> None:
    """Without MongoDB: the fake reports nothing present, so every latest event is missing."""
    delivered_event = _insert_event(
        migrated_engine,
        created_events,
        "usuario",
        str(uuid.uuid4()),
        "usuario.creado",
        datetime.now(UTC),
    )
    _set_delivery(migrated_engine, delivered_event, "ENTREGADO")
    unrouted_event = _insert_event(
        migrated_engine,
        created_events,
        "cita",
        str(uuid.uuid4()),
        "cita.actualizada",
        datetime.now(UTC),
    )

    _run_offline(migrated_engine)

    assert _delivery(migrated_engine, delivered_event)["estado"] == "PENDIENTE"
    inserted = _delivery(migrated_engine, unrouted_event)
    assert inserted is not None
    assert inserted["estado"] == "PENDIENTE"
    assert inserted["intentos"] == 0


def test_offline_second_run_leaves_a_pending_delivery_untouched(
    migrated_engine: Engine, created_events: list[str]
) -> None:
    event_id = _insert_event(
        migrated_engine,
        created_events,
        "usuario",
        str(uuid.uuid4()),
        "usuario.creado",
        datetime.now(UTC),
    )
    _set_delivery(migrated_engine, event_id, "ENTREGADO")
    _run_offline(migrated_engine)
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE entregas_eventos SET intentos = 2, ultimo_error = 'retry' "
                "WHERE evento_id = :e"
            ),
            {"e": event_id},
        )
    before = _delivery(migrated_engine, event_id)

    _run_offline(migrated_engine)

    assert _delivery(migrated_engine, event_id) == before

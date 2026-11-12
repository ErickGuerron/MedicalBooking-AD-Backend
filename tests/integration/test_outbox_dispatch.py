"""Outbox dispatch against SQL Server: claims, leases, retries and per-aggregate order (S6)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from medicalbooking.config import Settings
from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.modules.synchronization.application.ports import (
    DeliveryOutcome,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.modules.synchronization.infrastructure.repositories import SqlOutboxStore
from medicalbooking.shared.infrastructure.database import create_database_engine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Handler:
    """Records every event it receives; ``failure`` decides whether one call fails."""

    def __init__(
        self,
        code: str,
        failure: Callable[[OutboxEvent], Exception | None] | None = None,
    ) -> None:
        self.destination_code = code
        self.calls: list[OutboxEvent] = []
        self._failure = failure

    def handle(self, event: OutboxEvent) -> None:
        self.calls.append(event)
        error = self._failure(event) if self._failure else None
        if error is not None:
            raise error


@pytest.fixture
def aggregate(migrated_engine: Engine) -> Iterator[str]:
    aggregate_type = f"TEST_{uuid.uuid4().hex[:12]}"
    yield aggregate_type
    _delete_events(migrated_engine, aggregate_type)


@pytest.fixture
def destination(migrated_engine: Engine) -> Iterator[tuple[int, str]]:
    code = f"TEST_{uuid.uuid4().hex[:20]}"
    with migrated_engine.begin() as conn:
        destination_id = int(
            conn.execute(
                text(
                    "INSERT INTO destinos_sincronizacion (codigo, activo) "
                    "OUTPUT INSERTED.id VALUES (:codigo, 1)"
                ),
                {"codigo": code},
            ).scalar_one()
        )
    yield destination_id, code
    with migrated_engine.begin() as conn:
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


def _seed_event(engine: Engine, aggregate_type: str, aggregate_id: str, version: int) -> str:
    event_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, "
                "payload, version_agregado, creado_en) "
                "VALUES (:id, :tipo, :agregado, 'prueba', '{}', :version, :ahora)"
            ),
            {
                "id": event_id,
                "tipo": aggregate_type,
                "agregado": aggregate_id,
                "version": version,
                "ahora": NOW.replace(tzinfo=None),
            },
        )
    return event_id


def _row(engine: Engine, event_id: str, destination_id: int):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT estado, intentos, proximo_intento, bloqueado_hasta, ultimo_error, "
                "entregado_en FROM entregas_eventos "
                "WHERE evento_id = :evento AND destino_id = :destino"
            ),
            {"evento": event_id, "destino": destination_id},
        ).one()


def _calls_for(handler: _Handler, aggregate_type: str) -> list[int]:
    """Versions of this test's aggregate that the handler received, in call order."""
    return [e.aggregate_version for e in handler.calls if e.aggregate_type == aggregate_type]


def _service(engine: Engine, handler: _Handler, clock: _Clock) -> OutboxDispatchService:
    return OutboxDispatchService(store=SqlOutboxStore(engine), handlers=[handler], clock=clock)


def test_a_successful_delivery_is_marked_entregado(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    handler = _Handler(code)

    _service(migrated_engine, handler, _Clock(NOW)).run_cycle()

    row = _row(migrated_engine, event_id, destination_id)
    assert row.estado == "ENTREGADO"
    assert row.intentos == 1
    assert row.entregado_en == NOW.replace(tzinfo=None)
    assert row.bloqueado_hasta is None


def test_a_transient_failure_schedules_a_retry_with_the_error_class_only(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    handler = _Handler(code, lambda e: TransientDeliveryError("host=db user=secret"))

    _service(migrated_engine, handler, _Clock(NOW)).run_cycle()

    row = _row(migrated_engine, event_id, destination_id)
    assert row.estado == "ERROR_TEMPORAL"
    assert row.intentos == 1
    assert row.proximo_intento == (NOW + timedelta(minutes=1)).replace(tzinfo=None)
    assert row.bloqueado_hasta is None
    assert row.ultimo_error == "TransientDeliveryError"


def test_a_permanent_error_is_stored_as_error_permanente(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    handler = _Handler(code, lambda e: PermanentDeliveryError("invalid"))

    _service(migrated_engine, handler, _Clock(NOW)).run_cycle()

    row = _row(migrated_engine, event_id, destination_id)
    assert row.estado == "ERROR_PERMANENTE"
    assert row.ultimo_error == "PermanentDeliveryError"


def test_a_retry_is_not_claimed_before_it_is_due_and_is_delivered_after(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    failures = [TransientDeliveryError("first attempt")]
    handler = _Handler(code, lambda e: failures.pop() if failures else None)
    clock = _Clock(NOW)
    service = _service(migrated_engine, handler, clock)
    service.run_cycle()

    clock.now = NOW + timedelta(seconds=30)
    service.run_cycle()
    assert _calls_for(handler, aggregate) == [1]

    clock.now = NOW + timedelta(minutes=1)
    service.run_cycle()
    assert _calls_for(handler, aggregate) == [1, 1]
    assert _row(migrated_engine, event_id, destination_id).estado == "ENTREGADO"


def test_a_later_event_waits_until_the_earlier_one_of_its_aggregate_is_delivered(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    aggregate_id = str(uuid.uuid4())
    first = _seed_event(migrated_engine, aggregate, aggregate_id, 1)
    second = _seed_event(migrated_engine, aggregate, aggregate_id, 2)
    failed_once: list[bool] = []

    def fail_first_event_once(event: OutboxEvent) -> Exception | None:
        if event.aggregate_version == 1 and not failed_once:
            failed_once.append(True)
            return TransientDeliveryError()
        return None

    handler = _Handler(code, fail_first_event_once)
    clock = _Clock(NOW)
    service = _service(migrated_engine, handler, clock)

    service.run_cycle()
    assert _calls_for(handler, aggregate) == [1]
    assert _row(migrated_engine, second, destination_id).estado == "PENDIENTE"

    clock.now = NOW + timedelta(minutes=1)
    service.run_cycle()
    assert _calls_for(handler, aggregate) == [1, 1, 2]
    assert _row(migrated_engine, first, destination_id).estado == "ENTREGADO"
    assert _row(migrated_engine, second, destination_id).estado == "ENTREGADO"


def test_a_leased_delivery_cannot_be_claimed_again_until_the_lease_expires(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    _, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    store = SqlOutboxStore(migrated_engine)
    store.materialize()

    first = _claims_for(store.claim_due(NOW, NOW + timedelta(minutes=5), (code,), 1000), event_id)
    second = _claims_for(store.claim_due(NOW, NOW + timedelta(minutes=5), (code,), 1000), event_id)
    after_expiry = _claims_for(
        store.claim_due(NOW + timedelta(minutes=6), NOW + timedelta(minutes=11), (code,), 1000),
        event_id,
    )

    assert len(first) == 1
    assert second == []
    assert len(after_expiry) == 1


def test_a_stale_lease_cannot_overwrite_the_outcome_of_a_newer_claim(
    migrated_engine: Engine, aggregate: str, destination: tuple[int, str]
) -> None:
    destination_id, code = destination
    event_id = _seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1)
    store = SqlOutboxStore(migrated_engine)
    store.materialize()
    stale = _claims_for(store.claim_due(NOW, NOW + timedelta(minutes=5), (code,), 1000), event_id)[
        0
    ]
    fresh = _claims_for(
        store.claim_due(NOW + timedelta(minutes=6), NOW + timedelta(minutes=11), (code,), 1000),
        event_id,
    )[0]

    later = NOW + timedelta(minutes=6)
    stale_applied = store.complete(stale, DeliveryOutcome("ENTREGADO", 1), later)
    fresh_applied = store.complete(fresh, DeliveryOutcome("ENTREGADO", 1), later)

    assert stale_applied is False
    assert fresh_applied is True
    assert _row(migrated_engine, event_id, destination_id).estado == "ENTREGADO"


def test_two_concurrent_dispatchers_never_deliver_the_same_event_twice(
    migrated_engine: Engine,
    test_settings: Settings,
    aggregate: str,
    destination: tuple[int, str],
) -> None:
    destination_id, code = destination
    event_ids = [_seed_event(migrated_engine, aggregate, str(uuid.uuid4()), 1) for _ in range(40)]
    delivered: list[UUID] = []
    lock = threading.Lock()

    class _SlowRecordingHandler(_Handler):
        def handle(self, event: OutboxEvent) -> None:
            time.sleep(0.005)
            with lock:
                delivered.append(event.id)

    handler = _SlowRecordingHandler(code)
    engines = [create_database_engine(test_settings) for _ in range(2)]
    try:
        services = [
            OutboxDispatchService(
                store=SqlOutboxStore(engine),
                handlers=[handler],
                clock=_Clock(NOW),
                batch_size=5,
            )
            for engine in engines
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            for future in [pool.submit(service.run_cycle) for service in services]:
                future.result()
    finally:
        for engine in engines:
            engine.dispose()

    assert len(delivered) == len(set(delivered))
    assert {UUID(event_id) for event_id in event_ids} <= set(delivered)
    for event_id in event_ids:
        row = _row(migrated_engine, event_id, destination_id)
        assert row.estado == "ENTREGADO"
        assert row.intentos == 1


def _claims_for(claims, event_id: str) -> list:
    return [claim for claim in claims if str(claim.event.id) == event_id]

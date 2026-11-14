"""Outbox dispatch rules with a fake store and fake handlers (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from medicalbooking.modules.synchronization.application.dispatch_service import (
    OutboxDispatchService,
)
from medicalbooking.modules.synchronization.application.ports import (
    DeliveryClaim,
    DeliveryOutcome,
    PermanentDeliveryError,
    TransientDeliveryError,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
EVENT = OutboxEvent(
    id=UUID(int=1),
    aggregate_type="cita",
    aggregate_id=UUID(int=2),
    event_type="cita.reservada",
    aggregate_version=1,
    created_at=NOW,
)


class FakeStore:
    def __init__(self, batches: list[list[DeliveryClaim]] | None = None) -> None:
        self._batches = list(batches or [])
        self.materialize_calls = 0
        self.claim_destination_codes: list[tuple[str, ...]] = []
        self.outcomes: list[tuple[DeliveryClaim, DeliveryOutcome]] = []

    def materialize(self) -> int:
        self.materialize_calls += 1
        return 0

    def claim_due(self, now, lease_until, destination_codes, limit):
        self.claim_destination_codes.append(destination_codes)
        return self._batches.pop(0) if self._batches else []

    def complete(self, claim, outcome, now) -> bool:
        self.outcomes.append((claim, outcome))
        return True


class FakeHandler:
    def __init__(self, code: str = "NOTIFICATIONS", error: Exception | None = None) -> None:
        self.destination_code = code
        self._error = error
        self.handled: list[OutboxEvent] = []

    def handle(self, event: OutboxEvent) -> None:
        self.handled.append(event)
        if self._error is not None:
            raise self._error


def _claim(attempts: int = 0, code: str = "NOTIFICATIONS") -> DeliveryClaim:
    return DeliveryClaim(
        delivery_id="entrega-1",
        destination_code=code,
        attempts=attempts,
        lease_until=NOW + timedelta(minutes=5),
        event=EVENT,
    )


def _run(claims: list[DeliveryClaim], handler: FakeHandler):
    store = FakeStore([claims] if claims else [])
    service = OutboxDispatchService(store=store, handlers=[handler], clock=lambda: NOW)
    report = service.run_cycle()
    return store, report


def _only_outcome(store: FakeStore) -> DeliveryOutcome:
    assert len(store.outcomes) == 1
    return store.outcomes[0][1]


def test_a_successful_delivery_is_recorded_as_entregado() -> None:
    handler = FakeHandler()

    store, report = _run([_claim()], handler)

    assert handler.handled == [EVENT]
    outcome = _only_outcome(store)
    assert outcome.status == "ENTREGADO"
    assert outcome.attempts == 1
    assert outcome.error_code is None
    assert report.delivered == 1


def test_a_transient_failure_schedules_the_first_retry_one_minute_later() -> None:
    handler = FakeHandler(error=TransientDeliveryError("provider timeout"))

    store, report = _run([_claim(attempts=0)], handler)

    outcome = _only_outcome(store)
    assert outcome.status == "ERROR_TEMPORAL"
    assert outcome.attempts == 1
    assert outcome.next_attempt_at == NOW + timedelta(minutes=1)
    assert outcome.error_code == "TransientDeliveryError"
    assert report.retry_scheduled == 1


def test_the_second_failure_waits_two_minutes() -> None:
    handler = FakeHandler(error=TransientDeliveryError())

    store, _ = _run([_claim(attempts=1)], handler)

    outcome = _only_outcome(store)
    assert outcome.attempts == 2
    assert outcome.next_attempt_at == NOW + timedelta(minutes=2)


def test_the_backoff_stops_growing_at_thirty_two_minutes() -> None:
    handler = FakeHandler(error=TransientDeliveryError())

    store, _ = _run([_claim(attempts=6)], handler)

    outcome = _only_outcome(store)
    assert outcome.attempts == 7
    assert outcome.next_attempt_at == NOW + timedelta(minutes=32)


def test_an_unexpected_exception_is_transient_and_only_its_class_name_is_stored() -> None:
    handler = FakeHandler(error=RuntimeError("postgres://user:secret@db-host/app"))

    store, _ = _run([_claim()], handler)

    outcome = _only_outcome(store)
    assert outcome.status == "ERROR_TEMPORAL"
    assert outcome.error_code == "RuntimeError"
    assert "secret" not in outcome.error_code


def test_a_permanent_error_stops_retries_at_once() -> None:
    handler = FakeHandler(error=PermanentDeliveryError("invalid event"))

    store, report = _run([_claim(attempts=0)], handler)

    outcome = _only_outcome(store)
    assert outcome.status == "ERROR_PERMANENTE"
    assert outcome.attempts == 1
    assert outcome.next_attempt_at is None
    assert outcome.error_code == "PermanentDeliveryError"
    assert report.permanent_failures == 1


def test_a_failure_before_the_tenth_attempt_still_retries() -> None:
    handler = FakeHandler(error=TransientDeliveryError())

    store, _ = _run([_claim(attempts=8)], handler)

    assert _only_outcome(store).status == "ERROR_TEMPORAL"
    assert _only_outcome(store).attempts == 9


def test_the_tenth_failed_attempt_ends_in_permanent_error() -> None:
    handler = FakeHandler(error=TransientDeliveryError())

    store, _ = _run([_claim(attempts=9)], handler)

    outcome = _only_outcome(store)
    assert outcome.status == "ERROR_PERMANENTE"
    assert outcome.attempts == 10
    assert outcome.next_attempt_at is None


def test_only_destinations_with_a_handler_are_claimed() -> None:
    store = FakeStore()
    service = OutboxDispatchService(store=store, handlers=[FakeHandler()], clock=lambda: NOW)

    service.run_cycle()

    assert store.materialize_calls == 1
    assert store.claim_destination_codes == [("NOTIFICATIONS",)]


def test_with_no_handler_nothing_is_claimed_but_deliveries_are_still_materialized() -> None:
    store = FakeStore()
    service = OutboxDispatchService(store=store, handlers=[], clock=lambda: NOW)

    report = service.run_cycle()

    assert store.materialize_calls == 1
    assert store.claim_destination_codes == []
    assert report.delivered == 0


def test_a_failing_claim_does_not_stop_the_other_claims_in_the_batch() -> None:
    failing = FakeHandler(error=TransientDeliveryError())
    store = FakeStore([[_claim(), _claim()]])
    service = OutboxDispatchService(store=store, handlers=[failing], clock=lambda: NOW)

    service.run_cycle()

    assert [outcome.status for _, outcome in store.outcomes] == ["ERROR_TEMPORAL", "ERROR_TEMPORAL"]


def test_two_handlers_for_the_same_destination_are_rejected() -> None:
    with pytest.raises(ValueError):
        OutboxDispatchService(
            store=FakeStore(),
            handlers=[FakeHandler(), FakeHandler()],
            clock=lambda: NOW,
        )

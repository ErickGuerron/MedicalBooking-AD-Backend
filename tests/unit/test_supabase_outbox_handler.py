"""SUPABASE outbox destination: projects each event without a broker, mapping errors to outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest

from medicalbooking.modules.replica.infrastructure import supabase_projection
from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SUPABASE_CONSUMER,
    SupabaseOutboxHandler,
)
from medicalbooking.modules.synchronization.application.ports import (
    PermanentDeliveryError,
    TransientDeliveryError,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

EVENT = OutboxEvent(
    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    aggregate_type="orden_laboratorio",
    aggregate_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    event_type="orden_laboratorio.creada",
    aggregate_version=3,
    created_at=datetime(2026, 11, 12, 18, 0, tzinfo=UTC),
    payload={"estado": "SOLICITADA"},
)


def _record_apply(monkeypatch, error: Exception | None = None) -> dict:
    calls: dict = {}

    def fake_apply(envelope, sink, consumer):
        calls.update(envelope=envelope, sink=sink, consumer=consumer)
        if error is not None:
            raise error
        return True

    monkeypatch.setattr(supabase_projection, "apply_event", fake_apply)
    return calls


def test_the_destination_code_is_supabase() -> None:
    assert SupabaseOutboxHandler(connect=lambda: None).destination_code == "SUPABASE"


def test_an_event_is_applied_with_its_envelope_and_the_supabase_consumer(monkeypatch) -> None:
    calls = _record_apply(monkeypatch)

    SupabaseOutboxHandler(connect=lambda: None).handle(EVENT)

    assert calls["consumer"] == SUPABASE_CONSUMER == "replica-supabase"
    assert calls["envelope"] == {
        "event_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "aggregate_type": "orden_laboratorio",
        "aggregate_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "event_type": "orden_laboratorio.creada",
        "aggregate_version": 3,
        "created_at": "2026-11-12T18:00:00+00:00",
        "payload": {"estado": "SOLICITADA"},
    }


def test_a_database_driver_error_is_transient_and_hides_its_message(monkeypatch) -> None:
    _record_apply(monkeypatch, error=psycopg.OperationalError("password=hunter2"))

    with pytest.raises(TransientDeliveryError) as raised:
        SupabaseOutboxHandler(connect=lambda: None).handle(EVENT)

    assert "hunter2" not in str(raised.value)


@pytest.mark.parametrize("error", [TypeError("x"), ValueError("x"), AttributeError("x")])
def test_a_payload_that_does_not_fit_the_projection_is_permanent(monkeypatch, error) -> None:
    _record_apply(monkeypatch, error=error)

    with pytest.raises(PermanentDeliveryError):
        SupabaseOutboxHandler(connect=lambda: None).handle(EVENT)


def test_a_missing_field_is_skipped_with_a_warning_and_no_error(monkeypatch, caplog) -> None:
    _record_apply(monkeypatch, error=KeyError("estado"))

    with caplog.at_level("WARNING"):
        SupabaseOutboxHandler(connect=lambda: None).handle(EVENT)

    assert "orden_laboratorio.creada" in caplog.text
    assert "estado" in caplog.text


def test_an_empty_required_field_is_skipped_with_a_warning_and_no_error(
    monkeypatch, caplog
) -> None:
    _record_apply(
        monkeypatch,
        error=psycopg.errors.NotNullViolation(
            'null value in column "estado" of relation "citas" violates not-null constraint'
        ),
    )

    with caplog.at_level("WARNING"):
        SupabaseOutboxHandler(connect=lambda: None).handle(EVENT)

    assert "orden_laboratorio.creada" in caplog.text
    assert "relation" not in caplog.text

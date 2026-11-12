"""Outbox handler for the NOTIFICACIONES destination: which events notify, and idempotency."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.notifications.application.handler import NotificationOutboxHandler
from medicalbooking.modules.notifications.application.ports import PatientContact
from medicalbooking.modules.synchronization.application.ports import PermanentDeliveryError
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
EVENT_ID = UUID("11111111-1111-1111-1111-111111111111")
APPOINTMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
ORDER_ID = UUID("33333333-3333-3333-3333-333333333333")
PATIENT = PatientContact(user_id="44444444-4444-4444-4444-444444444444", email="ana@example.com")


class _Repository:
    def __init__(self, contact: PatientContact | None = PATIENT) -> None:
        self._contact = contact
        self.rows: dict[str, object] = {}

    def find_patient_contact(
        self, aggregate_type: str, aggregate_id: UUID
    ) -> PatientContact | None:
        return self._contact

    def enqueue(self, notification, now) -> bool:
        if notification.key in self.rows:
            return False
        self.rows[notification.key] = notification
        return True


def _event(event_type: str, aggregate_type: str, aggregate_id: UUID = APPOINTMENT_ID, **kw):
    return OutboxEvent(
        id=kw.get("event_id", EVENT_ID),
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        aggregate_version=1,
        created_at=NOW,
        payload=kw.get("payload", {}),
    )


def _handler(repository: _Repository) -> NotificationOutboxHandler:
    return NotificationOutboxHandler(repository=repository, clock=lambda: NOW)


def test_destination_code_is_the_seeded_notifications_destination() -> None:
    assert _handler(_Repository()).destination_code == "NOTIFICACIONES"


def test_booked_appointment_creates_one_notification_for_the_patient() -> None:
    repository = _Repository()

    _handler(repository).handle(_event("cita.reservada", "cita"))

    assert len(repository.rows) == 1
    row = next(iter(repository.rows.values()))
    assert row.key == f"{EVENT_ID}:CITA_RESERVADA"
    assert row.tipo == "CITA_RESERVADA"
    assert row.destinatario == "ana@example.com"
    assert row.usuario_id == PATIENT.user_id


def test_cancelled_appointment_creates_its_own_notification() -> None:
    repository = _Repository()

    _handler(repository).handle(_event("cita.cancelada", "cita"))

    assert [row.tipo for row in repository.rows.values()] == ["CITA_CANCELADA"]


def test_lab_result_ready_creates_a_notification_for_the_order_patient() -> None:
    repository = _Repository()

    _handler(repository).handle(
        _event("resultado_laboratorio.registrado", "orden_laboratorio", ORDER_ID)
    )

    assert [row.tipo for row in repository.rows.values()] == ["RESULTADO_LABORATORIO"]


def test_same_event_delivered_twice_creates_one_row_and_both_calls_succeed() -> None:
    repository = _Repository()
    handler = _handler(repository)
    event = _event("cita.reservada", "cita")

    handler.handle(event)
    handler.handle(event)

    assert len(repository.rows) == 1


@pytest.mark.parametrize(
    ("event_type", "aggregate_type"),
    [
        ("cita.expirada", "cita"),
        ("orden_laboratorio.creada", "orden_laboratorio"),
        ("revision_laboratorio.registrada", "orden_laboratorio"),
        ("propuesta.creada", "propuesta"),
    ],
)
def test_events_that_are_not_patient_facing_create_nothing(event_type, aggregate_type) -> None:
    repository = _Repository()

    _handler(repository).handle(_event(event_type, aggregate_type))

    assert repository.rows == {}


def test_missing_patient_is_a_permanent_delivery_error() -> None:
    repository = _Repository(contact=None)

    with pytest.raises(PermanentDeliveryError):
        _handler(repository).handle(_event("cita.reservada", "cita"))

    assert repository.rows == {}


def test_clinical_payload_never_reaches_the_notification_row() -> None:
    repository = _Repository()
    event = _event(
        "resultado_laboratorio.registrado",
        "orden_laboratorio",
        ORDER_ID,
        payload={"detalle_id": "Hemoglobina 7.2 g/dL", "estado_orden": "COMPLETADA"},
    )

    _handler(repository).handle(event)

    row = next(iter(repository.rows.values()))
    assert "Hemoglobina" not in repr(row)
    assert "7.2" not in repr(row)

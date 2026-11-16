"""Outbox handler: turns patient- and doctor-facing outbox events into notification rows.

Idempotent by ``clave_idempotencia = "<event id>:<template>"``: the dispatcher may deliver the
same event again after a crash, and the second insert is a no-op that still reports success.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from medicalbooking.modules.notifications.application.ports import (
    NewNotification,
    NotificationRepository,
    PatientContact,
)
from medicalbooking.modules.notifications.domain.templates import (
    DOCTOR_TEMPLATES,
    PAGO_COMPLETADO,
    RESULTADO_LABORATORIO,
    templates_for,
)
from medicalbooking.modules.synchronization.application.ports import PermanentDeliveryError
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

DESTINATION_CODE = "NOTIFICACIONES"


def _payment_state(event: OutboxEvent) -> str | None:
    pago = event.payload.get("pago")
    return pago.get("estado") if isinstance(pago, dict) else None


class NotificationOutboxHandler:
    destination_code = DESTINATION_CODE

    def __init__(self, repository: NotificationRepository, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._clock = clock

    def handle(self, event: OutboxEvent) -> None:
        for template in templates_for(event):
            if not self._applies(template, event):
                continue
            contact = self._contact_for(template, event)
            if contact is None:
                # The event points at an aggregate without that person: retries cannot fix that.
                raise PermanentDeliveryError("Notification event has no contact for its recipient")
            self._repository.enqueue(
                NewNotification(
                    key=f"{event.id}:{template}",
                    event_id=event.id,
                    usuario_id=contact.user_id,
                    tipo=template,
                    destinatario=contact.email,
                ),
                self._clock(),
            )

    @staticmethod
    def _applies(template: str, event: OutboxEvent) -> bool:
        if template == RESULTADO_LABORATORIO:
            # One message per order: the results go out when the laboratory completes it.
            return event.payload.get("estado_orden") == "COMPLETADA"
        if template == PAGO_COMPLETADO:
            # pago.actualizado fires for every state; only a confirmed payment is mailed.
            return _payment_state(event) == "CONFIRMADO"
        return True

    def _contact_for(self, template: str, event: OutboxEvent) -> PatientContact | None:
        if template in DOCTOR_TEMPLATES:
            return self._repository.find_doctor_contact(event.aggregate_type, event.aggregate_id)
        return self._repository.find_patient_contact(event.aggregate_type, event.aggregate_id)

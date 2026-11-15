"""Outbox handler: turns patient-facing outbox events into notification rows.

Idempotent by ``clave_idempotencia = "<event id>:<template>"``: the dispatcher may deliver the
same event again after a crash, and the second insert is a no-op that still reports success.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from medicalbooking.modules.notifications.application.ports import (
    NewNotification,
    NotificationRepository,
)
from medicalbooking.modules.notifications.domain.templates import (
    PAGO_COMPLETADO,
    RESULTADO_LABORATORIO,
    template_for,
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
        template = template_for(event)
        if template is None:
            return
        if template == RESULTADO_LABORATORIO and event.payload.get("estado_orden") != "COMPLETADA":
            # One message per order: the results go out when the laboratory completes it.
            return
        if template == PAGO_COMPLETADO and _payment_state(event) != "CONFIRMADO":
            # pago.actualizado fires for every state; only a confirmed payment is mailed.
            return
        contact = self._repository.find_patient_contact(event.aggregate_type, event.aggregate_id)
        if contact is None:
            # The event points at an aggregate without a patient: retries cannot fix that.
            raise PermanentDeliveryError("Notification event has no patient contact")
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

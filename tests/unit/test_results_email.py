"""The results email: the completed order PDF goes to the patient, built at send time."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from medicalbooking.modules.laboratory.application.order_report import render_order_report
from medicalbooking.modules.laboratory.application.ports import LabDetail, LabOrder, LabResult
from medicalbooking.modules.notifications.application.dispatcher import NotificationDispatcher
from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    EmailAttachment,
    EmailMessage,
)
from medicalbooking.modules.notifications.infrastructure.brevo import BrevoNotificationSender

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
ORDER_ID = "11111111-2222-3333-4444-555555555555"


def _order() -> LabOrder:
    return LabOrder(
        id=ORDER_ID,
        paciente_id="p",
        paciente_user_id="u",
        medico_id="m",
        medico_user_id="mu",
        prioridad="NORMAL",
        estado="COMPLETADA",
        observaciones=None,
        details=(LabDetail(id="d1", tipo_prueba_id="t", estado="COMPLETADO"),),
    )


class _Store:
    def __init__(self, order: LabOrder | None) -> None:
        self._order = order

    def get_order(self, order_id: str):
        return self._order if self._order and self._order.id == order_id else None

    def list_results(self, order_id: str):
        return [
            LabResult(
                id="r1",
                detalle_id="d1",
                nombre_analito="Ferritina",
                valor_texto=None,
                valor_numerico=9.0,
                unidad="ug/L",
                referencia_min=None,
                referencia_max=None,
                interpretacion=None,
            )
        ]

    def report_parties(self, order_id: str):
        return None


class _Queue:
    def __init__(self, due: list[DueNotification]) -> None:
        self.due = due
        self.sent: list[str] = []
        self.failed: list[str] = []

    def claim_due(self, now, lease_until, limit):
        return self.due

    def mark_sent(self, notification_id, provider_message_id, now):
        self.sent.append(notification_id)

    def mark_attempt_failed(self, notification_id, attempts, error_code, next_attempt_at, now):
        self.failed.append(error_code)


class _Sender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str | None:
        self.messages.append(message)
        return "msg-1"


def test_render_order_report_builds_the_pdf_of_an_existing_order_only() -> None:
    pdf = render_order_report(_Store(_order()), ORDER_ID, NOW)

    assert pdf is not None and pdf.startswith(b"%PDF-")
    assert render_order_report(_Store(None), ORDER_ID, NOW) is None


def test_dispatcher_attaches_the_results_pdf_built_for_the_due_order() -> None:
    attachment = EmailAttachment(
        name="resultados-1111.pdf", content_type="application/pdf", data=b"%PDF-x"
    )
    queue = _Queue(
        [
            DueNotification(
                id="n1",
                tipo="RESULTADO_LABORATORIO",
                destinatario="p@test.test",
                numero_intentos=0,
                aggregate_id=ORDER_ID,
            )
        ]
    )
    sender = _Sender()
    seen: list[str | None] = []

    def attachments_for(due: DueNotification) -> tuple[EmailAttachment, ...]:
        seen.append(due.aggregate_id)
        return (attachment,)

    report = NotificationDispatcher(queue, sender, lambda: NOW, attachments_for).send_due(10)

    assert report.sent == 1
    assert seen == [ORDER_ID]
    assert sender.messages[0].attachments == (attachment,)
    assert "adjuntos en PDF" in sender.messages[0].text


def test_failed_pdf_build_is_a_retry_with_a_code_and_no_message_sent() -> None:
    from medicalbooking.modules.notifications.application.ports import NotificationError

    queue = _Queue(
        [
            DueNotification(
                id="n1",
                tipo="RESULTADO_LABORATORIO",
                destinatario="p@test.test",
                numero_intentos=0,
                aggregate_id=str(uuid4()),
            )
        ]
    )
    sender = _Sender()

    def attachments_for(due):
        raise NotificationError("ORDER_NOT_FOUND")

    report = NotificationDispatcher(queue, sender, lambda: NOW, attachments_for).send_due(10)

    assert report.failed == 1
    assert queue.failed == ["ORDER_NOT_FOUND"]
    assert sender.messages == []


def test_brevo_sends_the_attachment_as_base64() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"messageId": "<m@brevo>"})

    sender = BrevoNotificationSender(
        api_key="test-key",
        sender_email="no-reply@medilo.test",
        sender_name="Medilo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    message = EmailMessage(
        to="p@test.test",
        subject="Tus resultados",
        text="Hola.",
        attachments=(
            EmailAttachment(name="r.pdf", content_type="application/pdf", data=b"%PDF-1"),
        ),
    )

    sender.send(message)

    assert captured["attachment"] == [
        {"name": "r.pdf", "content": base64.b64encode(b"%PDF-1").decode()}
    ]


def test_brevo_message_without_attachments_has_no_attachment_key() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={})

    sender = BrevoNotificationSender(
        api_key="k",
        sender_email="a@b.test",
        sender_name="Medilo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sender.send(EmailMessage(to="p@test.test", subject="s", text="t"))

    assert "attachment" not in captured

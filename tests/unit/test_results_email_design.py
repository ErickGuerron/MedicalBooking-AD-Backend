"""The results email: an HTML body for clients that render it, the plain text kept as a fallback.

The markup is static, so it carries no patient data. Brevo receives both parts.
"""

from __future__ import annotations

import json

import httpx

from medicalbooking.modules.notifications.application.ports import EmailMessage
from medicalbooking.modules.notifications.domain.templates import RESULTADO_LABORATORIO, TEMPLATES
from medicalbooking.modules.notifications.infrastructure.brevo import BrevoNotificationSender


def test_the_results_mail_has_an_html_body_and_keeps_the_plain_text_part() -> None:
    template = TEMPLATES[RESULTADO_LABORATORIO]

    assert template.html is not None
    assert "<html" in template.html
    assert "adjuntos en PDF" in template.text
    assert "PDF de resultados" in template.html


def test_the_html_is_static_and_carries_no_patient_data() -> None:
    html = TEMPLATES[RESULTADO_LABORATORIO].html

    assert html is not None
    assert "{" not in html
    assert "@" not in html


def test_brevo_receives_the_html_and_the_text_with_the_attachment() -> None:
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(201, json={"messageId": "<m@example.test>"})

    sender = BrevoNotificationSender(
        api_key="unused-in-tests",
        sender_email="medilo@example.test",
        sender_name="Medilo",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sender.send(
        EmailMessage(to="paciente@example.test", subject="s", text="t", html="<html></html>")
    )

    assert sent["htmlContent"] == "<html></html>"
    assert sent["textContent"] == "t"

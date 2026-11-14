"""Brevo sender adapter for notifications, tested with a mock HTTP transport (never the network)."""

from __future__ import annotations

import json

import httpx
import pytest

from medicalbooking.modules.notifications.application.ports import EmailMessage, NotificationError
from medicalbooking.modules.notifications.infrastructure.brevo import (
    BREVO_SEND_URL,
    BrevoNotificationSender,
)

API_KEY = "xkeyn-test-api-key-value"
MESSAGE = EmailMessage(
    to="ana@example.com",
    subject="Tienes una actualización en tu cita",
    text="Hola.\n\nTienes una actualización en tu cita.\n",
)


def _sender(handler) -> BrevoNotificationSender:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BrevoNotificationSender(
        api_key=API_KEY,
        sender_email="no-reply@example.com",
        sender_name="Medilo",
        client=client,
    )


def test_posts_the_message_with_the_api_key_header_and_returns_the_provider_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"messageId": "<abc@smtp-relay>"})

    message_id = _sender(handler).send(MESSAGE)

    request = seen[0]
    assert str(request.url) == BREVO_SEND_URL
    assert request.headers["api-key"] == API_KEY
    body = json.loads(request.content)
    assert body["to"] == [{"email": "ana@example.com"}]
    assert body["subject"] == MESSAGE.subject
    assert body["textContent"] == MESSAGE.text
    assert message_id == "<abc@smtp-relay>"


def test_api_key_is_never_part_of_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"messageId": "m"})

    _sender(handler).send(MESSAGE)

    assert API_KEY.encode() not in seen[0].content


def test_provider_rejection_surfaces_a_code_without_the_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"bad key {API_KEY}"})

    with pytest.raises(NotificationError) as raised:
        _sender(handler).send(MESSAGE)

    assert raised.value.code == "BREVO_HTTP_401"
    assert API_KEY not in str(raised.value)


def test_transport_failure_surfaces_a_code_without_the_exception_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection to host failed for {API_KEY}")

    with pytest.raises(NotificationError) as raised:
        _sender(handler).send(MESSAGE)

    assert raised.value.code == "BREVO_UNREACHABLE"
    assert API_KEY not in str(raised.value)


def test_success_without_a_json_body_returns_no_provider_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"")

    assert _sender(handler).send(MESSAGE) is None

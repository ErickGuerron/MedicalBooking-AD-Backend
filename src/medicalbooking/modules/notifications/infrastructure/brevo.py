"""Brevo transactional email adapter for patient notifications.

Endpoint and authentication: POST https://api.brevo.com/v3/smtp/email with the ``api-key``
header. Failures surface as a NotificationError whose code names the HTTP status or the
transport class. The response body, the exception text, the api key and the message text are
never included, so none of them can reach the logs or the database.
"""

from __future__ import annotations

import httpx

from medicalbooking.modules.notifications.application.ports import EmailMessage, NotificationError

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
_MAX_PROVIDER_ID_LENGTH = 255


class BrevoNotificationSender:
    def __init__(
        self,
        api_key: str,
        sender_email: str,
        sender_name: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender_email, "name": sender_name}
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, message: EmailMessage) -> str | None:
        payload = {
            "sender": self._sender,
            "to": [{"email": message.to}],
            "subject": message.subject,
            "textContent": message.text,
        }
        try:
            response = self._client.post(
                BREVO_SEND_URL,
                headers={"api-key": self._api_key, "accept": "application/json"},
                json=payload,
            )
        except httpx.HTTPError:
            raise NotificationError("BREVO_UNREACHABLE") from None
        if response.status_code >= 300:
            raise NotificationError(f"BREVO_HTTP_{response.status_code}")
        return _provider_id(response)


def _provider_id(response: httpx.Response) -> str | None:
    try:
        message_id = response.json().get("messageId")
    except ValueError:
        return None
    if not isinstance(message_id, str):
        return None
    return message_id[:_MAX_PROVIDER_ID_LENGTH]

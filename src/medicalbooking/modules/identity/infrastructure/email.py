"""Brevo transactional email adapter for account activation.

Endpoint and authentication: POST https://api.brevo.com/v3/smtp/email with the
``api-key`` header. The activation link carries a single-use token, so neither the
link nor the token is ever written to logs or to error messages.

Templates: the HTML is compiled from ``email-templates/activation/activation.mjml`` by
the Node build step (see ``email-templates/build.mjs``) and committed under
``email_templates/``. The
plain-text version lives next to it. Both use ``string.Template`` placeholders; values are
HTML-escaped for the HTML part only. Node is never needed at runtime.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from string import Template

import httpx

from medicalbooking.modules.identity.application.ports import MailDeliveryError

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
SUBJECT = "Activa tu cuenta de Medilo"
TEMPLATE_DIR = Path(__file__).parent / "email_templates"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


@cache
def _read_template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def render_activation_email(first_name: str, link: str, asset_base_url: str) -> RenderedEmail:
    # Images must be absolute URLs: mail clients cannot resolve relative paths.
    html_body = _read_template("activation.html").substitute(
        first_name=html.escape(first_name),
        link=html.escape(link, quote=True),
        asset_base_url=html.escape(asset_base_url.rstrip("/"), quote=True),
    )
    text_body = _read_template("activation.txt").substitute(
        first_name=first_name,
        link=link,
    )
    return RenderedEmail(subject=SUBJECT, html=html_body, text=text_body)


class BrevoActivationMailer:
    def __init__(
        self,
        api_key: str,
        sender_email: str,
        sender_name: str,
        asset_base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = {"email": sender_email, "name": sender_name}
        self._asset_base_url = asset_base_url
        self._client = client or httpx.Client(timeout=10.0)

    def send_activation(self, to_email: str, first_name: str, link: str) -> None:
        rendered = render_activation_email(first_name, link, self._asset_base_url)
        payload = {
            "sender": self._sender,
            "to": [{"email": to_email, "name": first_name}],
            "subject": rendered.subject,
            "htmlContent": rendered.html,
            "textContent": rendered.text,
        }
        try:
            response = self._client.post(
                BREVO_SEND_URL,
                headers={"api-key": self._api_key, "accept": "application/json"},
                json=payload,
            )
        except httpx.HTTPError:
            # Fixed message: the exception text could include request details.
            raise MailDeliveryError("Activation email could not be sent") from None
        if response.status_code >= 300:
            raise MailDeliveryError(
                f"Brevo rejected the activation email (HTTP {response.status_code})"
            )

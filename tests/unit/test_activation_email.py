"""Activation email templates: rendering, escaping, and sent payload."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from medicalbooking.modules.identity.infrastructure.email import (
    BrevoActivationMailer,
    render_activation_email,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LINK = "https://api.example.test/api/v1/auth/verify-email?token=abc123&lang=es"
ASSET_BASE = "https://cdn.example.test/email-assets/email-templates/assets"
PRIMARY_COLOR = "#FF7A1A"


def test_rendered_html_uses_palette_and_fills_placeholders() -> None:
    rendered = render_activation_email("Ana", LINK, ASSET_BASE)

    assert PRIMARY_COLOR in rendered.html
    assert "Ana" in rendered.html
    assert "$first_name" not in rendered.html
    assert "$link" not in rendered.html
    assert "$asset_base_url" not in rendered.html
    assert "Activar mi cuenta" in rendered.html


def test_rendered_html_loads_every_image_from_the_configured_asset_base() -> None:
    rendered = render_activation_email("Ana", LINK, ASSET_BASE + "/")

    assert "../assets/" not in rendered.html
    assert f"{ASSET_BASE}/brand/logo.png" in rendered.html
    assert f"{ASSET_BASE}/layout/header.png" in rendered.html
    assert f"{ASSET_BASE}/illustrations/divisor-pulso.png" in rendered.html
    assert f"{ASSET_BASE}/icons/icono-citas.png" in rendered.html


def test_rendered_html_declares_spanish_language() -> None:
    rendered = render_activation_email("Ana", LINK, ASSET_BASE)

    assert '<html lang="es"' in rendered.html
    assert 'lang="und"' not in rendered.html


def test_rendered_html_escapes_user_supplied_name_and_link() -> None:
    rendered = render_activation_email("<script>alert(1)</script>", LINK, ASSET_BASE)

    assert "<script>alert(1)</script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "token=abc123&amp;lang=es" in rendered.html


def test_plain_text_contains_name_and_raw_link() -> None:
    rendered = render_activation_email("Ana", LINK, ASSET_BASE)

    assert "Hola Ana" in rendered.text
    assert LINK in rendered.text
    assert "$link" not in rendered.text


def test_brevo_payload_carries_the_rendered_templates() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "test"})

    mailer = BrevoActivationMailer(
        api_key="test-key",
        sender_email="sender@example.test",
        sender_name="MedicalBooking",
        asset_base_url=ASSET_BASE,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    mailer.send_activation("ana@example.test", "Ana", LINK)

    expected = render_activation_email("Ana", LINK, ASSET_BASE)
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["htmlContent"] == expected.html
    assert payload["textContent"] == expected.text
    assert payload["subject"] == expected.subject


@pytest.mark.skipif(
    shutil.which("node") is None or not (BACKEND_ROOT / "node_modules" / "mjml").exists(),
    reason="Node.js and npm dependencies are needed to recompile the MJML source",
)
def test_committed_html_matches_mjml_source() -> None:
    result = subprocess.run(  # noqa: S603
        ["node", "email-templates/build.mjs", "--check"],  # noqa: S607
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr or result.stdout

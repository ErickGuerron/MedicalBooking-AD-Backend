"""Front-end URLs used by email links: defaults and environment overrides."""

from __future__ import annotations

import pytest

from medicalbooking.config import Settings


def test_default_front_urls_use_the_reserved_local_ports() -> None:
    settings = Settings(_env_file=None)

    assert settings.flutter_front_url == "http://localhost:8081"
    assert settings.angular_front_url == "http://localhost:4200"


def test_front_urls_are_read_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLUTTER_FRONT_URL", "https://portal.medilo.example")
    monkeypatch.setenv("ANGULAR_FRONT_URL", "https://sistema.medilo.example")

    settings = Settings(_env_file=None)

    assert settings.flutter_front_url == "https://portal.medilo.example"
    assert settings.angular_front_url == "https://sistema.medilo.example"

from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from medicalbooking.app import create_app
from medicalbooking.config import Settings


def _client(**overrides):
    settings = Settings(_env_file=None, sqlserver_port=1, sqlserver_database="unused", **overrides)
    return create_app(settings).test_client()


def test_liveness_names_the_configured_machine_and_api_instance() -> None:
    response = _client(machine_name="MediloVM1", replica_id="api-1").get("/health/live")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "machine": "MediloVM1", "api": "api-1"}


def test_liveness_falls_back_to_the_local_machine_name() -> None:
    response = _client().get("/health/live")

    assert response.get_json() == {
        "status": "ok",
        "machine": socket.gethostname(),
        "api": "local",
    }


@pytest.mark.parametrize("bad", ["", "vm 1", "vm/1", "a" * 65, "vm\r\nX-Injected: 1"])
def test_machine_name_must_be_a_safe_value(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, machine_name=bad)


def test_machine_name_default_is_unset() -> None:
    assert Settings(_env_file=None).machine_name is None

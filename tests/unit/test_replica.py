from __future__ import annotations

import pytest
from pydantic import ValidationError

from medicalbooking.app import create_app
from medicalbooking.config import Settings


def _app(replica_id: str):
    settings = Settings(
        _env_file=None,
        sqlserver_port=1,
        sqlserver_database="unused",
        replica_id=replica_id,
    )
    return create_app(settings)


def test_every_response_identifies_the_replica() -> None:
    client = _app("api-3").test_client()

    live = client.get("/health/live")
    missing = client.get("/does-not-exist")

    assert live.headers["X-Replica-Id"] == "api-3"
    assert missing.headers["X-Replica-Id"] == "api-3"


@pytest.mark.parametrize("bad", ["", "api 1", "api/1", "a" * 65, "api\r\nX-Injected: 1"])
def test_replica_id_must_be_a_safe_header_value(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, replica_id=bad)


def test_replica_id_default_is_local() -> None:
    assert Settings(_env_file=None).replica_id == "local"

import pytest
from pydantic import SecretStr

from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.fallback_read import FallbackReadService
from medicalbooking.modules.replica.infrastructure.factory import (
    ReplicaConfigError,
    build_fallback_read_service,
)

SECRET_READER = "postgresql://replica_reader.ref:SUPER-SECRET-PASS@pooler.example:5432/postgres"
SECRET_AUDIT = "mongodb+srv://replica_auditor:OTHER-SECRET-PASS@cluster.example/"


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "supabase_reader_database_url": None,
        "mongodb_auditor_uri": None,
        "mongodb_database": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_missing_settings_fail_closed_naming_only_the_variables():
    settings = _settings(
        supabase_reader_database_url=SecretStr(SECRET_READER),
        mongodb_auditor_uri=None,
        mongodb_database="Medilo",
    )

    with pytest.raises(ReplicaConfigError) as raised:
        build_fallback_read_service(settings)

    message = str(raised.value)
    assert "MONGODB_AUDITOR_URI" in message
    assert "SUPER-SECRET-PASS" not in message
    assert "OTHER-SECRET-PASS" not in message


def test_every_missing_variable_is_listed():
    with pytest.raises(ReplicaConfigError) as raised:
        build_fallback_read_service(_settings())

    message = str(raised.value)
    for name in ("SUPABASE_READER_DATABASE_URL", "MONGODB_AUDITOR_URI", "MONGODB_DATABASE"):
        assert name in message


def test_complete_settings_build_the_service_without_connecting():
    settings = _settings(
        supabase_reader_database_url=SecretStr(SECRET_READER),
        mongodb_auditor_uri=SecretStr(SECRET_AUDIT),
        mongodb_database="Medilo",
    )

    service = build_fallback_read_service(settings)

    assert isinstance(service, FallbackReadService)

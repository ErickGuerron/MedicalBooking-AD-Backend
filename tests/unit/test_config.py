from __future__ import annotations

from urllib.parse import unquote_plus

import pytest
from pydantic import ValidationError

from medicalbooking.config import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None keeps a developer's local .env from leaking into unit tests.
    return Settings(_env_file=None, **overrides)


def test_defaults_target_local_default_instance_with_windows_auth() -> None:
    settings = _settings()

    connection_string = settings.odbc_connection_string
    assert "SERVER=localhost,1433" in connection_string
    assert "DATABASE={MedicalBooking}" in connection_string
    assert "Trusted_Connection=yes" in connection_string
    assert "UID=" not in connection_string
    assert "TrustServerCertificate=yes" in connection_string


def test_sql_auth_escapes_braces_in_password() -> None:
    settings = _settings(sqlserver_username="app", sqlserver_password="p{w}d;x")

    connection_string = settings.odbc_connection_string
    assert "UID={app}" in connection_string
    # ODBC: values are wrapped in braces and each '}' is doubled; '{' needs no escape.
    assert "PWD={p{w}}d;x}" in connection_string
    assert "Trusted_Connection" not in connection_string


def test_sqlalchemy_url_is_url_encoded_odbc_connect() -> None:
    settings = _settings()

    url = settings.sqlalchemy_url
    assert url.startswith("mssql+pyodbc:///?odbc_connect=")
    assert unquote_plus(url.split("odbc_connect=", 1)[1]) == settings.odbc_connection_string


def test_production_refuses_trusted_server_certificate() -> None:
    with pytest.raises(ValidationError, match="TRUST_SERVER_CERTIFICATE"):
        _settings(
            app_env="production",
            sqlserver_trust_server_certificate=True,
            jwt_signing_key="x" * 32,
        )


def test_production_requires_jwt_signing_key() -> None:
    with pytest.raises(ValidationError, match="JWT_SIGNING_KEY"):
        _settings(app_env="production", sqlserver_trust_server_certificate=False)


def test_production_accepts_hardened_settings() -> None:
    settings = _settings(
        app_env="production",
        sqlserver_host="sql.example.test",
        sqlserver_trust_server_certificate=False,
        sqlserver_username="app",
        sqlserver_password="secret",
        jwt_signing_key="x" * 32,
    )

    assert "TrustServerCertificate=no" in settings.odbc_connection_string
    assert "Encrypt=yes" in settings.odbc_connection_string


def test_secrets_are_not_shown_in_repr() -> None:
    settings = _settings(sqlserver_username="app", sqlserver_password="very-secret-value")

    assert "very-secret-value" not in repr(settings)

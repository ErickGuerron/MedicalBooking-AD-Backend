"""Validated, environment-driven settings.

Secrets are read from environment variables or a git-ignored ``.env`` file and are
never logged. The same code runs against local SQL Server and Azure SQL; only the
connection settings change between environments.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _odbc_value(value: str) -> str:
    """Escape an ODBC attribute value by wrapping it in braces."""
    return "{" + value.replace("}", "}}") + "}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"

    # Identifies one replica (for example api-1). Echoed in X-Replica-Id so load
    # balancing across instances can be observed; it carries no secrets.
    replica_id: str = Field(default="local", pattern=r"^[A-Za-z0-9._-]{1,64}$")

    sqlserver_host: str = "localhost"
    sqlserver_port: int = 1433
    sqlserver_database: str = "MedicalBooking"
    # Empty username means Windows authentication (Trusted_Connection).
    sqlserver_username: str = ""
    sqlserver_password: SecretStr = SecretStr("")
    sqlserver_driver: str = "ODBC Driver 18 for SQL Server"
    sqlserver_encrypt: bool = True
    # Accepted only for local development with a self-signed certificate.
    sqlserver_trust_server_certificate: bool = True

    jwt_signing_key: SecretStr | None = None

    @model_validator(mode="after")
    def _check_production_safety(self) -> Settings:
        if self.app_env == "production":
            if self.sqlserver_trust_server_certificate:
                raise ValueError("SQLSERVER_TRUST_SERVER_CERTIFICATE must be 'no' in production")
            if not self.sqlserver_encrypt:
                raise ValueError("SQLSERVER_ENCRYPT must be enabled in production")
            if self.jwt_signing_key is None:
                raise ValueError("JWT_SIGNING_KEY is required in production")
        return self

    @property
    def odbc_connection_string(self) -> str:
        parts = [
            f"DRIVER={_odbc_value(self.sqlserver_driver)}",
            f"SERVER={self.sqlserver_host},{self.sqlserver_port}",
            f"DATABASE={_odbc_value(self.sqlserver_database)}",
            f"Encrypt={'yes' if self.sqlserver_encrypt else 'no'}",
            f"TrustServerCertificate={'yes' if self.sqlserver_trust_server_certificate else 'no'}",
        ]
        if self.sqlserver_username:
            parts.append(f"UID={_odbc_value(self.sqlserver_username)}")
            parts.append(f"PWD={_odbc_value(self.sqlserver_password.get_secret_value())}")
        else:
            parts.append("Trusted_Connection=yes")
        return ";".join(parts)

    @property
    def sqlalchemy_url(self) -> str:
        return "mssql+pyodbc:///?odbc_connect=" + quote_plus(self.odbc_connection_string)

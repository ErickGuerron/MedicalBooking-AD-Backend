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
    jwt_access_ttl_minutes: int = Field(default=15, ge=1, le=60)

    # How long a pending appointment holds its slot before payment (payment phase, E4).
    appointment_hold_minutes: int = Field(default=15, ge=1, le=120)
    # External knowledge sources (importers only). Secrets never leave Settings in plain text.
    loinc_username: str | None = None
    loinc_password: SecretStr | None = None
    icd11_client_id: str | None = None
    icd11_client_secret: SecretStr | None = None
    # Private PDF storage root (E3). Single-host only until shared storage exists (E7).
    lab_documents_dir: str = Field(default="storage/documents", min_length=1, max_length=260)

    # Transactional email for account verification (Brevo HTTP API). Secrets stay in
    # environment variables or the git-ignored .env file.
    # Stripe signing secret of the webhook endpoint (test or live). Without it the webhook
    # fails closed with 503: no event is trusted without verification.
    stripe_webhook_secret: SecretStr | None = None
    # Stripe secret key for Checkout Sessions (test key locally). Without it checkout answers
    # 503 and creates nothing. Never logged.
    stripe_secret_key: SecretStr | None = None
    brevo_api_key: SecretStr | None = None
    mail_from: str | None = None
    mail_from_name: str = Field(default="MedicalBooking", min_length=1, max_length=70)
    # Absolute public URL of the email images (for example a public Supabase Storage folder).
    # Mail clients cannot load relative paths, so the value must start with https://.
    email_assets_base_url: str = Field(default="", max_length=500)
    # Public base URL used to build the verification link. The link points to the API
    # until a front end exists; change APP_PUBLIC_URL when one does.
    app_public_url: str = Field(default="http://localhost:5000", min_length=8, max_length=500)
    email_verification_hours: int = Field(default=24, ge=1, le=168)
    # Front-end URLs for links in emails and notifications. Local development defaults:
    # Flutter patient portal on 8081 (8080 is taken on the development machine), Angular
    # transactional system on 4200 (the Angular dev server default).
    flutter_front_url: str = Field(default="http://localhost:8081", min_length=8, max_length=500)
    angular_front_url: str = Field(default="http://localhost:4200", min_length=8, max_length=500)

    # Serves the OpenAPI document and Scalar page. Unset means on outside production
    # and off in production; set API_DOCS_ENABLED explicitly to override either way.
    api_docs_enabled: bool | None = None

    @model_validator(mode="after")
    def _check_signing_key_strength(self) -> Settings:
        # HS256 needs at least 256 bits of entropy; shorter keys are brute-forceable.
        if self.jwt_signing_key is not None and len(self.jwt_signing_key.get_secret_value()) < 32:
            raise ValueError("JWT_SIGNING_KEY must be at least 32 characters")
        return self

    @model_validator(mode="after")
    def _check_email_assets_url(self) -> Settings:
        if self.email_assets_base_url and not self.email_assets_base_url.startswith("https://"):
            raise ValueError("EMAIL_ASSETS_BASE_URL must be an absolute https:// URL")
        return self

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
    def docs_enabled(self) -> bool:
        if self.api_docs_enabled is None:
            return self.app_env != "production"
        return self.api_docs_enabled

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

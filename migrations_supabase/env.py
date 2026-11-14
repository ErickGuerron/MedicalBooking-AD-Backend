"""Alembic environment for the Supabase replica (schema ``replica``), online only.

Uses the owner connection SUPABASE_DATABASE_URL. The version table lives in the
``replica`` schema, not in ``public``: Supabase exposes ``public`` through its REST API.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from medicalbooking.config import Settings

VERSION_SCHEMA = "replica"


def _database_url() -> str:
    secret = Settings().supabase_database_url
    if secret is None:
        raise RuntimeError("SUPABASE_DATABASE_URL is not set")
    url = secret.get_secret_value()
    # The driver is psycopg 3; SQLAlchemy needs it named explicitly.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    raise NotImplementedError(
        "Offline migrations are not supported; run 'alembic -c alembic_supabase.ini upgrade head'."
    )


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.begin() as connection:
        # Alembic creates its version table before the first revision runs.
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {VERSION_SCHEMA}")
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            transactional_ddl=True,
            version_table_schema=VERSION_SCHEMA,
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

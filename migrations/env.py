"""Alembic environment: online migrations only, against SQL Server / Azure SQL.

The URL comes from ``Settings`` unless a caller (for example tests) sets
``sqlalchemy.url`` on the Alembic config first.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from medicalbooking.config import Settings

config = context.config


def _database_url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    url = explicit or Settings().sqlalchemy_url
    # Alembic config is an interpolating ConfigParser; URL-encoded parts contain '%'.
    return url.replace("%", "%%")


def run_migrations_offline() -> None:
    raise NotImplementedError(
        "Offline migrations are not supported; run 'alembic upgrade head' against the database."
    )


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

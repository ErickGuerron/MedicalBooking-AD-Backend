"""Copy the authorization catalog (roles, permissions and links) to the Supabase replica.

Run after a migration that changes roles or permissions:

    uv run python -m medicalbooking.workers.sync_replica_catalog

Required settings: SQLSERVER_* (source) and SUPABASE_WRITER_DATABASE_URL (target).
The catalog holds no personal data. The whole catalog is replaced in one transaction, so a
failed run leaves the previous catalog in place. Only counts are printed: the connection
strings never are.
"""

from __future__ import annotations

import logging
import sys

import psycopg

from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.catalog_sync import replace_catalog
from medicalbooking.modules.replica.infrastructure.supabase_catalog import (
    SupabaseCatalogTarget,
    read_catalog,
)
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
    except Exception as error:
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    if settings.supabase_writer_database_url is None:
        logger.error("worker configuration incomplete: missing SUPABASE_WRITER_DATABASE_URL")
        return 1

    engine = create_database_engine(settings)
    try:
        snapshot = read_catalog(engine)
        writer_url = settings.supabase_writer_database_url.get_secret_value()
        target = SupabaseCatalogTarget(lambda: psycopg.connect(writer_url, connect_timeout=15))
        replace_catalog(snapshot, target)
    except Exception as error:
        # Log the error type only: driver messages may carry connection data.
        logger.error("catalog sync failed: %s", type(error).__name__)
        return 1
    finally:
        engine.dispose()
    print(
        f"catalog synced roles={len(snapshot.roles)} permisos={len(snapshot.permisos)} "
        f"links={len(snapshot.roles_permisos)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

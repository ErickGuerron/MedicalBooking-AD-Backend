"""Copy the authorization catalog and the read-only directory to the replicas.

Run after a migration that changes roles or permissions, and on a schedule so doctors and
services stay current in both replicas:

    uv run python -m medicalbooking.workers.sync_replica_catalog

Required settings: SQLSERVER_* (source), plus SUPABASE_WRITER_DATABASE_URL (Supabase target)
and/or MONGODB_PROJECTION_URI with MONGODB_DATABASE (MongoDB target). A target that is not
configured is skipped. Each target is replaced as a whole in one transaction, so a failed run
leaves the previous data in place. The directory holds staff names and catalog text only. Only
counts are printed: the connection strings never are.
"""

from __future__ import annotations

import logging
import sys

import psycopg
from pymongo import MongoClient

from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.catalog_sync import replace_catalog
from medicalbooking.modules.replica.application.directory_sync import replace_directory
from medicalbooking.modules.replica.infrastructure.mongo_directory import replace_directory_mongo
from medicalbooking.modules.replica.infrastructure.supabase_catalog import (
    SupabaseCatalogTarget,
    read_catalog,
)
from medicalbooking.modules.replica.infrastructure.supabase_directory import read_directory
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
    supabase_configured = settings.supabase_writer_database_url is not None
    mongo_configured = settings.mongodb_projection_uri is not None and bool(
        settings.mongodb_database
    )
    if not supabase_configured and not mongo_configured:
        logger.error("worker configuration incomplete: no replica target is configured")
        return 1

    engine = create_database_engine(settings)
    try:
        catalog = read_catalog(engine)
        directory = read_directory(engine)
    except Exception as error:
        # Log the error type only: driver messages may carry connection data.
        logger.error("catalog sync failed reading the source: %s", type(error).__name__)
        engine.dispose()
        return 1

    # Each target is independent: a Supabase migration that is not applied yet must not stop the
    # MongoDB directory, and the other way around.
    failed: list[str] = []
    try:
        if supabase_configured:
            writer_url = settings.supabase_writer_database_url.get_secret_value()
            target = SupabaseCatalogTarget(lambda: psycopg.connect(writer_url, connect_timeout=15))
            replace_catalog(catalog, target)
            replace_directory(directory, target)
    except Exception as error:
        logger.error("catalog sync failed for supabase: %s", type(error).__name__)
        failed.append("supabase")
    try:
        if mongo_configured:
            client = MongoClient(
                settings.mongodb_projection_uri.get_secret_value(), serverSelectionTimeoutMS=5000
            )
            replace_directory_mongo(directory, client[settings.mongodb_database])
    except Exception as error:
        logger.error("directory sync failed for mongodb: %s", type(error).__name__)
        failed.append("mongodb")
    finally:
        engine.dispose()
    if failed:
        return 1
    print(
        f"catalog synced roles={len(catalog.roles)} permisos={len(catalog.permisos)} "
        f"links={len(catalog.roles_permisos)} especialidades={len(directory.especialidades)} "
        f"medicos={len(directory.medicos)} servicios={len(directory.servicios)} "
        f"citas_medico={len(directory.citas_medico)} "
        f"supabase={'on' if supabase_configured else 'off'} "
        f"mongodb={'on' if mongo_configured else 'off'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

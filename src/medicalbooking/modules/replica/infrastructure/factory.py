"""Composition of the fallback read path from settings (spec 8.3, S8/S9).

Connections are created lazily: building the service never opens a socket, so a
misconfigured replica fails on first use, and the process still starts when the
replicas are not needed. Missing settings are reported by name only, never by value.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from pymongo import MongoClient

from medicalbooking.config import Settings
from medicalbooking.modules.replica.application.fallback_read import FallbackReadService
from medicalbooking.modules.replica.infrastructure.mongo_audit import MongoAuditSink
from medicalbooking.modules.replica.infrastructure.supabase_reader import SupabaseReplicaReader

AUDIT_COLLECTION = "auditoria_accesos"


class ReplicaConfigError(Exception):
    """A required replica setting is missing. The message names variables, never values."""


def build_fallback_read_service(settings: Settings) -> FallbackReadService:
    missing = []
    if settings.supabase_reader_database_url is None:
        missing.append("SUPABASE_READER_DATABASE_URL")
    if settings.mongodb_auditor_uri is None:
        missing.append("MONGODB_AUDITOR_URI")
    if not settings.mongodb_database:
        missing.append("MONGODB_DATABASE")
    if missing:
        raise ReplicaConfigError("missing replica settings: " + ", ".join(missing))

    reader_url = settings.supabase_reader_database_url.get_secret_value()
    audit_uri = settings.mongodb_auditor_uri.get_secret_value()
    database = settings.mongodb_database

    # connect=False defers the connection, including the SRV DNS lookup, to first use.
    audit_collection = MongoClient(audit_uri, connect=False)[database][AUDIT_COLLECTION]
    reader = SupabaseReplicaReader(lambda: psycopg.connect(reader_url, connect_timeout=15))
    return FallbackReadService(
        sink=MongoAuditSink(audit_collection),
        reader=reader,
        resolver=reader,
        clock=lambda: datetime.now(UTC),
    )

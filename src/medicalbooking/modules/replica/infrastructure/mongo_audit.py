"""MongoDB Atlas audit sink for fallback reads (spec 8.3, S8/S9).

Uses the insert-only role replica_auditor. The sink only inserts: it never updates,
replaces or deletes history. Any failure becomes AuditUnavailable so the read is rejected.
"""

from __future__ import annotations

from typing import Protocol

from pymongo.errors import PyMongoError

from medicalbooking.modules.replica.application.fallback_read import (
    AuditRecord,
    AuditUnavailable,
)


class _InsertCollection(Protocol):
    def insert_one(self, document: dict) -> object: ...


class MongoAuditSink:
    def __init__(self, collection: _InsertCollection) -> None:
        self._collection = collection

    def append(self, record: AuditRecord) -> None:
        try:
            self._collection.insert_one(record.as_document())
        except PyMongoError as error:
            # Keep only the error type: driver messages may carry connection data.
            raise AuditUnavailable(type(error).__name__) from None

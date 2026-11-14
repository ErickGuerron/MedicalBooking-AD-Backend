"""Supabase presence check for replica reconciliation (read-only).

Runs as the reconciler role, which must see every row of replica.usuarios and replica.citas (see
docs/replica-reconciliation.md). Ids are compared as lower-case text, in batches of 500. The
destination code is KAFKA because Supabase is fed through the Kafka replication topic.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

DESTINATION_CODE = "KAFKA"
BATCH_SIZE = 500

_PRESENT_QUERY: dict[str, str] = {
    "usuario": "SELECT lower(id::text) FROM replica.usuarios WHERE lower(id::text) = ANY(%s)",
    "cita": "SELECT lower(id::text) FROM replica.citas WHERE lower(id::text) = ANY(%s)",
}


class SupabasePresenceCheck:
    destination_code = DESTINATION_CODE

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def present_ids(self, aggregate_type: str, ids: Sequence[str]) -> set[str]:
        query = _PRESENT_QUERY[aggregate_type]
        connection = self._connect()
        try:
            found: set[str] = set()
            with connection.cursor() as cursor:
                for start in range(0, len(ids), BATCH_SIZE):
                    batch = list(ids[start : start + BATCH_SIZE])
                    cursor.execute(query, (batch,))
                    found.update(str(row[0]) for row in cursor.fetchall())
            return found
        finally:
            connection.close()

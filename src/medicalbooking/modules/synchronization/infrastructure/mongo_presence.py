"""MONGODB destination presence check for replica reconciliation (read-only).

Looks up documents by ``_id`` in batches of 500. The projection writes each aggregate under its
aggregate id, so a document with that id means the aggregate is present.
"""

from __future__ import annotations

from collections.abc import Sequence

from pymongo.database import Database

DESTINATION_CODE = "MONGODB"
BATCH_SIZE = 500

# Collection that the projection writes for each tracked aggregate type.
COLLECTIONS: dict[str, str] = {"usuario": "usuarios", "cita": "citas"}


class MongoPresenceCheck:
    destination_code = DESTINATION_CODE

    def __init__(self, database: Database) -> None:
        self._database = database

    def present_ids(self, aggregate_type: str, ids: Sequence[str]) -> set[str]:
        collection = self._database[COLLECTIONS[aggregate_type]]
        found: set[str] = set()
        for start in range(0, len(ids), BATCH_SIZE):
            batch = list(ids[start : start + BATCH_SIZE])
            for document in collection.find({"_id": {"$in": batch}}, {"_id": 1}):
                found.add(str(document["_id"]).lower())
        return found

"""Private document storage in MongoDB GridFS, shared by every VM.

The local directory store (``storage.py``) keeps each PDF on the disk of the host that received
it, so a second VM cannot serve it. GridFS keeps the bytes in the database, which both VMs read.
The SQL row keeps the GridFS file id as its storage key, never a client-supplied path.

Deployment note: the Mongo user behind ``MONGODB_PROJECTION_URI`` needs ``readWrite`` on the
database, because GridFS writes the ``fs.files`` and ``fs.chunks`` collections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from bson import ObjectId
from bson.errors import InvalidId

from medicalbooking.config import Settings
from medicalbooking.modules.laboratory.infrastructure.storage import LocalDocumentStorage


class GridBucket(Protocol):
    """The part of ``gridfs.GridFS`` the store uses."""

    def put(self, data: bytes, **kwargs: Any) -> ObjectId: ...

    def get(self, file_id: ObjectId) -> Any: ...

    def delete(self, file_id: ObjectId) -> None: ...


class MongoDocumentStorage:
    def __init__(self, bucket: GridBucket) -> None:
        self._bucket = bucket

    def save(self, content: bytes) -> str:
        return str(self._bucket.put(content, filename="documento-laboratorio.pdf"))

    def read(self, key: str) -> bytes:
        return self._bucket.get(_object_id(key)).read()

    def delete(self, key: str) -> None:
        self._bucket.delete(_object_id(key))


def _object_id(key: str) -> ObjectId:
    try:
        return ObjectId(key)
    except (InvalidId, TypeError) as exc:
        raise ValueError("Invalid storage key") from exc


def build_document_storage(
    kind: str,
    *,
    root: Path,
    mongo_uri: str | None = None,
    mongo_database: str | None = None,
) -> LocalDocumentStorage | MongoDocumentStorage:
    """Pick the storage named by ``DOCUMENT_STORAGE``: ``local`` (default) or ``mongo``."""
    if kind == "local":
        return LocalDocumentStorage(root)
    if kind == "mongo":
        if not mongo_uri or not mongo_database:
            raise ValueError(
                "DOCUMENT_STORAGE=mongo needs MONGODB_PROJECTION_URI and MONGODB_DATABASE"
            )
        from gridfs import GridFS
        from pymongo import MongoClient

        database = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)[mongo_database]
        return MongoDocumentStorage(GridFS(database))
    raise ValueError(f"Unknown document storage: {kind}")


def document_storage_for(settings: Settings) -> LocalDocumentStorage | MongoDocumentStorage:
    """The storage the composition roots (API and workers) use for PDFs."""
    uri = (
        settings.mongodb_projection_uri.get_secret_value()
        if settings.mongodb_projection_uri
        else None
    )
    return build_document_storage(
        settings.document_storage,
        root=Path(settings.lab_documents_dir),
        mongo_uri=uri,
        mongo_database=settings.mongodb_database,
    )

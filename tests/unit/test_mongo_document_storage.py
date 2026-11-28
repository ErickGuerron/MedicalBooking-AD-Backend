"""PDF storage in MongoDB GridFS: keys, round trip, deletes and the storage selection."""

from __future__ import annotations

from pathlib import Path

import pytest
from bson import ObjectId

from medicalbooking.modules.laboratory.infrastructure.mongo_storage import (
    MongoDocumentStorage,
    build_document_storage,
)
from medicalbooking.modules.laboratory.infrastructure.storage import LocalDocumentStorage

PDF = b"%PDF-1.4 synthetic report"


class _Grid:
    """In-memory stand-in for gridfs.GridFS: put, get (with read) and delete by ObjectId."""

    def __init__(self) -> None:
        self.files: dict[ObjectId, bytes] = {}

    def put(self, data: bytes, **kwargs: object) -> ObjectId:
        file_id = ObjectId()
        self.files[file_id] = data
        return file_id

    def get(self, file_id: ObjectId):
        return _Stored(self.files[file_id])

    def delete(self, file_id: ObjectId) -> None:
        self.files.pop(file_id)


class _Stored:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_a_saved_pdf_is_read_back_under_an_object_id_key() -> None:
    storage = MongoDocumentStorage(_Grid())

    key = storage.save(PDF)

    assert len(key) == 24 and all(c in "0123456789abcdef" for c in key)
    assert storage.read(key) == PDF


def test_a_deleted_pdf_is_gone() -> None:
    grid = _Grid()
    storage = MongoDocumentStorage(grid)
    key = storage.save(PDF)

    storage.delete(key)

    assert grid.files == {}


@pytest.mark.parametrize("key", ["", "../../etc/passwd", "not-an-object-id", "a" * 23])
def test_a_key_that_is_not_an_object_id_is_refused(key: str) -> None:
    storage = MongoDocumentStorage(_Grid())

    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.read(key)


def test_local_is_the_default_storage(tmp_path: Path) -> None:
    storage = build_document_storage("local", root=tmp_path)

    assert isinstance(storage, LocalDocumentStorage)


def test_mongo_storage_needs_its_connection_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DOCUMENT_STORAGE=mongo"):
        build_document_storage("mongo", root=tmp_path)


def test_an_unknown_storage_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown document storage"):
        build_document_storage("s3", root=tmp_path)


def _mongo_settings(**overrides: object):
    from medicalbooking.config import Settings

    return Settings(_env_file=None, document_storage="mongo", mongodb_database="db", **overrides)


def test_mongo_storage_accepts_a_dedicated_documents_uri() -> None:
    settings = _mongo_settings(mongodb_documents_uri="mongodb://docs")

    assert settings.mongodb_documents_uri.get_secret_value() == "mongodb://docs"


def test_mongo_storage_still_accepts_the_projection_uri_alone() -> None:
    _mongo_settings(mongodb_projection_uri="mongodb://proj")


def test_mongo_storage_without_any_uri_is_refused() -> None:
    with pytest.raises(ValueError, match="MONGODB_DOCUMENTS_URI"):
        _mongo_settings()


def test_the_documents_uri_wins_over_the_projection_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    from medicalbooking.modules.laboratory.infrastructure import mongo_storage

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        mongo_storage, "build_document_storage", lambda kind, **kw: seen.update(kw) or kind
    )
    settings = _mongo_settings(
        mongodb_documents_uri="mongodb://docs", mongodb_projection_uri="mongodb://proj"
    )

    mongo_storage.document_storage_for(settings)

    assert seen["mongo_uri"] == "mongodb://docs"

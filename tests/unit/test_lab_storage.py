"""Private local document storage: round trip, opaque keys and no path escape (E3)."""

from __future__ import annotations

import pytest

from medicalbooking.modules.laboratory.infrastructure.storage import LocalDocumentStorage


@pytest.fixture
def storage(tmp_path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "documentos")


def test_saved_bytes_are_read_back_under_an_opaque_key(storage) -> None:
    key = storage.save(b"%PDF-1.4 synthetic")

    assert storage.read(key) == b"%PDF-1.4 synthetic"
    assert key.endswith(".bin") and "/" not in key and "\\" not in key


def test_keys_that_escape_the_root_are_refused(storage, tmp_path) -> None:
    (tmp_path / "secreto.txt").write_bytes(b"no")

    for hostile in ["../secreto.txt", "..\\secreto.txt", "/etc/passwd", "a/b.bin"]:
        with pytest.raises(ValueError):
            storage.read(hostile)


def test_delete_removes_the_file_and_tolerates_missing_keys(storage) -> None:
    key = storage.save(b"%PDF-1.4")

    storage.delete(key)
    storage.delete(key)

    with pytest.raises(FileNotFoundError):
        storage.read(key)


def test_two_saves_never_share_a_key(storage) -> None:
    assert storage.save(b"%PDF-a") != storage.save(b"%PDF-a")

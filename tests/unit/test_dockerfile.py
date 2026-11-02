"""The image must let the unprivileged app user write PDF storage.

Regression: /app is root-owned and the app runs as uid 10001, so a storage folder created
under /app failed every gunicorn worker at boot (container exited, liveness never answered).
"""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _lines() -> list[str]:
    return DOCKERFILE.read_text(encoding="utf-8").splitlines()


def test_the_document_folder_is_owned_by_the_app_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "mkdir -p /app/data/documents" in text
    assert "chown app:app /app/data/documents" in text


def test_the_storage_variable_points_to_that_folder() -> None:
    assert "ENV LAB_DOCUMENTS_DIR=/app/data/documents" in _lines()


def test_storage_is_prepared_before_the_process_drops_to_the_app_user() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    user_switch = text.index("USER app")
    assert text.index("chown app:app /app/data/documents") < user_switch
    assert text.index("ENV LAB_DOCUMENTS_DIR") < user_switch

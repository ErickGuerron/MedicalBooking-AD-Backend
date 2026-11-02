"""Private document storage on a local directory (E3).

Keys are generated here and match one strict pattern, so a key coming from the database
can never point outside the root. This is a single-host store: multi-VM deployment needs
shared or blob storage before E7 (spec: no binary inside the database).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_KEY = re.compile(r"^[0-9a-f]{32}\.bin$")


class LocalDocumentStorage:
    def __init__(self, root: Path) -> None:
        # Building the storage never touches the disk: workers boot even without write access.
        self._root = Path(root).resolve()

    def save(self, content: bytes) -> str:
        # The folder is created on the first write, not when the application starts.
        self._root.mkdir(parents=True, exist_ok=True)
        key = f"{uuid.uuid4().hex}.bin"
        with open(self._path(key), "xb") as handle:
            handle.write(content)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("Invalid storage key")
        path = (self._root / key).resolve()
        if path.parent != self._root:
            raise ValueError("Invalid storage key")
        return path

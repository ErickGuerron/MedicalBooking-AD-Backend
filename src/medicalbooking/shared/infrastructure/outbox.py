"""Append an outbox event inside the caller's SQL Server transaction.

The caller must already hold the lock on the aggregate row, so two writers cannot assign
the same version. The payload is the snapshot a replica needs; it must never carry
credentials or contact data.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text


def append_outbox(
    conn: Connection,
    aggregate_type: str,
    aggregate_id: str,
    event: str,
    payload: dict[str, Any],
    now: datetime,
) -> None:
    version = conn.execute(
        text(
            "SELECT ISNULL(MAX(version_agregado), 0) + 1 FROM eventos_outbox "
            "WHERE agregado_tipo = :t AND agregado_id = :a"
        ),
        {"t": aggregate_type, "a": aggregate_id},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, payload, "
            "version_agregado, creado_en) "
            "VALUES (:id, :t, :a, :tipo, :payload, :version, :ahora)"
        ),
        {
            "id": str(uuid.uuid4()),
            "t": aggregate_type,
            "a": aggregate_id,
            "tipo": event,
            "payload": json.dumps(payload),
            "version": version,
            "ahora": now,
        },
    )

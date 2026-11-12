"""The NOTIFICACIONES synchronization destination is seeded by a migration and active."""

from __future__ import annotations

from sqlalchemy import Connection, text


def test_notifications_destination_is_seeded_and_active(db: Connection) -> None:
    row = db.execute(
        text("SELECT activo FROM destinos_sincronizacion WHERE codigo = 'NOTIFICACIONES'")
    ).first()

    assert row is not None
    assert row.activo is True

"""Queue the day-of-appointment reminder for every confirmed appointment of today (Ecuador time).

Run once a day, early in the morning, before the outbox dispatcher and the mail sender:

    uv run python -m medicalbooking.workers.appointment_reminders

Each appointment gets exactly one reminder event in the outbox, however many times this runs.
The mail itself is then materialised and sent by outbox_dispatcher and send_notifications.
Exit code 0 means the run finished; 1 means it failed. Only counts are printed.
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, text

from medicalbooking.config import Settings
from medicalbooking.modules.notifications.infrastructure.email_render import ECUADOR_OFFSET
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)
REMINDER_EVENT = "cita.recordatorio"


def ecuador_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The UTC window of the Ecuador calendar day that contains ``now``."""
    local = now.astimezone(UTC) + ECUADOR_OFFSET
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = (start_local - ECUADOR_OFFSET).replace(tzinfo=None)
    return start_utc, start_utc + timedelta(days=1)


def queue_reminders(engine: Engine, now: datetime) -> int:
    start, end = ecuador_day_bounds(now)
    queued = 0
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id FROM citas c JOIN horarios_disponibles h ON h.id = c.horario_id "
                "WHERE c.estado = 'CONFIRMADA' AND h.inicio_utc >= :s AND h.inicio_utc < :e "
                "AND NOT EXISTS (SELECT 1 FROM eventos_outbox o WHERE o.agregado_id = c.id "
                "AND o.tipo_evento = :evento)"
            ),
            {"s": start, "e": end, "evento": REMINDER_EVENT},
        ).all()
        for row in rows:
            version = conn.execute(
                text(
                    "SELECT COALESCE(MAX(version_agregado), 0) + 1 FROM eventos_outbox "
                    "WHERE agregado_id = :c"
                ),
                {"c": row.id},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, "
                    "payload, version_agregado, creado_en) "
                    "VALUES (:id, 'cita', :c, :evento, '{}', :v, :ahora)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "c": row.id,
                    "evento": REMINDER_EVENT,
                    "v": version,
                    "ahora": now.astimezone(UTC).replace(tzinfo=None),
                },
            )
            queued += 1
    return queued


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
        engine = create_database_engine(settings)
    except Exception as error:
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    try:
        queued = queue_reminders(engine, datetime.now(UTC))
        print(f"reminders_queued={queued}")
        return 0
    except Exception as error:
        logger.error("reminder run failed: %s", type(error).__name__)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

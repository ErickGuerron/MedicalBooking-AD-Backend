"""Create bookable slots for every active doctor, from today until Sunday. Idempotent; never runs in production.

    uv run python scripts/seed_availability.py [--until YYYY-MM-DD] [--dry-run]

Slots run 08:00-17:00 branch time (UTC-5, the branch's clock) in steps of the doctor's
`duracion_cita_min`. Slots already in the past are skipped. Running it twice adds nothing.
The branch, one office per doctor and one open agenda per doctor are created when missing.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.database import create_database_engine

BRANCH_TZ = timezone(timedelta(hours=-5))  # shortcut: fixed offset; Ecuador has no DST
OPENS = time(8, 0)
CLOSES = time(17, 0)
BRANCH_NAME = "Sede principal"


def next_sunday(today: date) -> date:
    """Today when it is already Sunday, else the coming Sunday."""
    return today + timedelta(days=(6 - today.weekday()) % 7)


def build_slots(
    first_day: date, last_day: date, minutes: int, now_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """Naive-UTC (start, end) pairs between OPENS and CLOSES, skipping what already started."""
    step = timedelta(minutes=minutes)
    slots = []
    day = first_day
    while day <= last_day:
        start = datetime.combine(day, OPENS, BRANCH_TZ)
        close = datetime.combine(day, CLOSES, BRANCH_TZ)
        while start + step <= close:
            if start > now_utc:
                slots.append(
                    (
                        start.astimezone(timezone.utc).replace(tzinfo=None),
                        (start + step).astimezone(timezone.utc).replace(tzinfo=None),
                    )
                )
            start += step
        day += timedelta(days=1)
    return slots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", type=date.fromisoformat, help="last day, default the coming Sunday")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args()

    settings = Settings()
    if settings.app_env == "production":
        print("Refusing to seed availability in production.", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    today = now.astimezone(BRANCH_TZ).date()
    last_day = args.until or next_sunday(today)
    if last_day < today:
        print(f"--until {last_day} is before today ({today}).", file=sys.stderr)
        return 2

    engine = create_database_engine(settings)
    with engine.begin() as conn:
        doctors = conn.execute(
            text("SELECT id, duracion_cita_min FROM medicos WHERE activo = 1 ORDER BY registro_profesional")
        ).all()
        if not doctors:
            print("No active doctors. Run scripts/seed_synthetic.py or create one first.", file=sys.stderr)
            return 1
        branch_id = _get_or_create(conn, "sedes", "nombre", BRANCH_NAME, {})
        added = 0
        for number, doctor in enumerate(doctors, start=1):
            slots = build_slots(today, last_day, doctor.duracion_cita_min, now)
            if args.dry_run:
                print(f"doctor {number}: {len(slots)} slots between {today} and {last_day}")
                continue
            office_id = _get_or_create(
                conn, "consultorios", "nombre", f"Consultorio {number}", {"sede_id": branch_id}
            )
            agenda_id = _open_agenda(conn, str(doctor.id), office_id, today, last_day)
            for start, end in slots:
                added += conn.execute(
                    text(
                        "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
                        "SELECT :id, :a, :s, :e WHERE NOT EXISTS ("
                        "SELECT 1 FROM horarios_disponibles WHERE agenda_id = :a AND inicio_utc = :s)"
                    ),
                    {"id": str(uuid.uuid4()), "a": agenda_id, "s": start, "e": end},
                ).rowcount
    if not args.dry_run:
        print(f"{added} new slots for {len(doctors)} doctor(s), {today} to {last_day}.")
    return 0


def _get_or_create(conn, table: str, column: str, value: str, parent: dict[str, str]) -> str:
    """Id of the row named `value` (under the same parent), inserting it when absent."""
    where = "".join(f" AND {key} = :{key}" for key in parent)
    row = conn.execute(
        text(f"SELECT id FROM {table} WHERE {column} = :v{where}"), {"v": value, **parent}
    ).first()
    if row:
        return str(row.id)
    new_id = str(uuid.uuid4())
    columns = "".join(f", {key}" for key in parent)
    params = "".join(f", :{key}" for key in parent)
    conn.execute(
        text(f"INSERT INTO {table} (id, {column}{columns}) VALUES (:id, :v{params})"),
        {"id": new_id, "v": value, **parent},
    )
    return new_id


def _open_agenda(conn, doctor_id: str, office_id: str, first_day: date, last_day: date) -> str:
    """The doctor's active agenda covering the whole range; a new one when none does."""
    row = conn.execute(
        text(
            "SELECT id FROM agendas WHERE medico_id = :m AND activa = 1 AND fecha_inicio <= :d "
            "AND (fecha_fin IS NULL OR fecha_fin >= :f)"
        ),
        {"m": doctor_id, "d": first_day, "f": last_day},
    ).first()
    if row:
        return str(row.id)
    new_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
            "VALUES (:id, :m, :c, :d)"
        ),
        {"id": new_id, "m": doctor_id, "c": office_id, "d": first_day},
    )
    return new_id


if __name__ == "__main__":
    raise SystemExit(main())

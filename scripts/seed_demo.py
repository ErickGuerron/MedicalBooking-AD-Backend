"""Demo seed: accounts, doctor agendas, bookable slots and one booking per demo patient.

Accounts and bookings go through the application services (IdentityService.create_user and
SchedulingService.book), so each account and each booking writes its outbox event in the same
transaction as the data. Sedes, consultorios, agendas, slots and services are plain SQL: no
event exists for them, and the replicated appointment snapshot already carries the slot window.

    $env:AZURE_DATABASE_URL = "mssql+pyodbc://..."
    $env:SQLSERVER_DATABASE = "<the same database name>"
    uv run python scripts/seed_demo.py --confirm <exact database name> \
        --credentials-out C:\\secure\\demo-credentials.json

Guards: AZURE_DATABASE_URL is required and must name the database given in --confirm; the
settings database (SQLSERVER_DATABASE) must name the same database; APP_ENV=production needs
--allow-production; the credentials file must be outside this repository. Passwords are written
only to that file. Running it twice adds nothing: accounts already seeded are detected and skipped.

Accepted limitation: the account service stores only name, email, password and roles, so the
demo patients get no phone, birth date, emergency contact or smoking profile.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import Connection, Engine, MetaData, Table, func, insert, select
from sqlalchemy.engine import make_url

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.identity.domain.policies import PERM_USERS_ADMIN
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.shared.domain.errors import AppError

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DOMAIN = "demo.test"
BRANCH_TZ = timezone(timedelta(hours=-5))  # fixed offset, same convention as seed_availability
OPENS = time(8, 0)
CLOSES = time(17, 0)
SLOT_MINUTES = 30
PATIENT_COUNT = 6
DOCTOR_COUNT = 2
SERVICE_NAMES = ("Consulta general", "Control de laboratorio")
ACCOUNT_PLAN: tuple[tuple[str, str], ...] = (
    ("administrador", "ADMINISTRADOR"),
    ("laboratorista", "LABORATORISTA"),
    *((f"medico{n}", "MEDICO") for n in range(1, DOCTOR_COUNT + 1)),
    *((f"paciente{n}", "PACIENTE") for n in range(1, PATIENT_COUNT + 1)),
)
# The seed runs in-process, not as an HTTP caller. This actor is never stored: create_user reads
# only its permissions.
SEED_ACTOR = AccessProfile(
    user_id=str(uuid.UUID(int=0)),
    status="ACTIVO",
    roles=frozenset({"ADMINISTRADOR"}),
    permissions=frozenset({PERM_USERS_ADMIN}),
)


@dataclass(frozen=True)
class Account:
    role: str
    local: str
    user_id: str
    email: str
    password: str


@dataclass(frozen=True)
class Slot:
    id: str
    doctor_local: str
    start: datetime  # naive UTC


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a demonstration database.")
    parser.add_argument("--confirm", required=True, help="exact database name to seed")
    parser.add_argument("--credentials-out", required=True, help="file outside this repository")
    parser.add_argument("--days", type=int, default=14, help="days of slots from today")
    parser.add_argument("--seed", type=int, default=20261113, help="Faker seed")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="required when APP_ENV=production: the seed then writes demo accounts there",
    )
    args = parser.parse_args(argv)

    url = os.environ.get("AZURE_DATABASE_URL")
    if not url:
        print("Set AZURE_DATABASE_URL to the target database.", file=sys.stderr)
        return 2
    if os.environ.get("APP_ENV", "").lower() == "production" and not args.allow_production:
        print("APP_ENV=production: pass --allow-production to seed demo accounts.", file=sys.stderr)
        return 2
    database = make_url(url).database or ""
    if database != args.confirm:
        print("The database name must equal --confirm.", file=sys.stderr)
        return 2
    credentials_path = Path(args.credentials_out).resolve()
    if REPO_ROOT in credentials_path.parents or credentials_path == REPO_ROOT:
        print("Write the credentials file outside this repository.", file=sys.stderr)
        return 2
    settings = Settings()
    if settings.sqlserver_database != database:
        print(
            "SQLSERVER_DATABASE must name the same database as AZURE_DATABASE_URL.",
            file=sys.stderr,
        )
        return 2

    app = create_app(settings)
    engine: Engine = app.extensions["medicalbooking.engine"]
    metadata = MetaData()
    metadata.reflect(bind=engine)
    if "usuarios" not in metadata.tables or "eventos_outbox" not in metadata.tables:
        print("The schema is missing; run the migrations on this database first.", file=sys.stderr)
        return 2

    already = _demo_account_count(engine, metadata)
    if already:
        print(f"Already seeded: {already} demo accounts present; nothing added.")
        return 0

    outbox_before = _outbox_counts(engine, metadata)
    faker = Faker("es_MX")
    Faker.seed(args.seed)
    faker.seed_instance(args.seed)
    try:
        report, credentials = _seed(app, engine, metadata, faker, args.days)
    except (AppError, ValueError) as error:
        print(f"Seed aborted: {error}", file=sys.stderr)
        return 1
    _write_credentials(credentials_path, database, credentials)
    try:
        growth = _outbox_growth(outbox_before, _outbox_counts(engine, metadata), report)
    except ValueError as error:
        print(f"Seed incomplete: {error}", file=sys.stderr)
        return 1
    print(
        f"Seeded {report['usuarios']} accounts, {report['doctores']} doctors, "
        f"{report['horarios']} slots over {args.days} days and {report['citas']} bookings. "
        "Credentials written outside the repo."
    )
    print(
        f"Outbox: usuario.creado +{growth['usuario.creado']}, "
        f"cita.reservada +{growth['cita.reservada']}."
    )
    return 0


def _demo_account_count(engine: Engine, metadata: MetaData) -> int:
    users = metadata.tables["usuarios"]
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(users).where(users.c.email.like(f"%@{DEMO_DOMAIN}"))
        ).scalar_one()


def _outbox_counts(engine: Engine, metadata: MetaData) -> dict[str, int]:
    """Outbox events per type; the seed compares the counts before and after its run."""
    outbox = metadata.tables["eventos_outbox"]
    with engine.connect() as conn:
        rows = conn.execute(
            select(outbox.c.tipo_evento, func.count()).group_by(outbox.c.tipo_evento)
        ).all()
    return {event: count for event, count in rows}


def _outbox_growth(
    before: dict[str, int], after: dict[str, int], report: dict[str, int]
) -> dict[str, int]:
    """Each account must have produced usuario.creado and each booking cita.reservada."""
    expected = {"usuario.creado": report["usuarios"], "cita.reservada": report["citas"]}
    growth: dict[str, int] = {}
    short: list[str] = []
    for event, minimum in expected.items():
        grown = after.get(event, 0) - before.get(event, 0)
        growth[event] = grown
        if grown < minimum:
            short.append(f"{event} grew by {grown}, expected at least {minimum}")
    if short:
        raise ValueError("; ".join(short))
    return growth


def _write_credentials(path: Path, database: str, credentials: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "database": database,
                "generated_at": datetime.now(UTC).isoformat(),
                "note": "Demonstration accounts only. Never use in production.",
                "accounts": credentials,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _seed(
    app: Any, engine: Engine, metadata: MetaData, faker: Faker, days: int
) -> tuple[dict[str, int], list[dict[str, str]]]:
    accounts = _create_accounts(app.extensions["medicalbooking.identity"], faker)
    doctors = [account for account in accounts if account.role == "MEDICO"]
    patients = [account for account in accounts if account.role == "PACIENTE"]
    service_id, slots = _seed_schedule(engine, metadata, doctors, days)
    scheduling = app.extensions["medicalbooking.scheduling"]
    bookings = _book_slots(scheduling, engine, patients, slots, service_id)
    report = {
        "usuarios": len(accounts),
        "doctores": len(doctors),
        "horarios": len(slots),
        "citas": bookings,
    }
    credentials = [
        {"rol": account.role, "email": account.email, "password": account.password}
        for account in accounts
    ]
    return report, credentials


def _create_accounts(identity: Any, faker: Faker) -> list[Account]:
    """Every account goes through IdentityService.create_user, doctors included."""
    accounts: list[Account] = []
    for local, role in ACCOUNT_PLAN:
        email = f"{local}@{DEMO_DOMAIN}"
        password = secrets.token_urlsafe(12)
        user_id = identity.create_user(
            SEED_ACTOR,
            email=email,
            password=password,
            first_name=faker.first_name(),
            last_name=faker.last_name(),
            roles=[role],
            professional_registration=f"DEMO-{local.upper()}" if role == "MEDICO" else None,
        )
        accounts.append(
            Account(role=role, local=local, user_id=user_id, email=email, password=password)
        )
    return accounts


def _seed_schedule(
    engine: Engine, metadata: MetaData, doctors: list[Account], days: int
) -> tuple[str, list[Slot]]:
    """Plain SQL: sede, consultorios, agendas, slots and services have no outbox event."""
    today = datetime.now(BRANCH_TZ).date()
    now = datetime.now(UTC).replace(tzinfo=None)
    slots: list[Slot] = []
    with engine.begin() as conn:
        sede_id = str(uuid.uuid4())
        _insert(
            conn,
            metadata,
            "sedes",
            {
                "id": sede_id,
                "nombre": "Sede Demo Principal",
                "direccion": "Av. Demo 123",
                "zona_horaria": "America/Guayaquil",
                "activo": True,
            },
        )
        for number, doctor in enumerate(doctors, start=1):
            office_id = str(uuid.uuid4())
            _insert(
                conn,
                metadata,
                "consultorios",
                {
                    "id": office_id,
                    "sede_id": sede_id,
                    "nombre": f"Consultorio demo {number}",
                    "activo": True,
                },
            )
            agenda_id = str(uuid.uuid4())
            _insert(
                conn,
                metadata,
                "agendas",
                {
                    "id": agenda_id,
                    "medico_id": _medico_id(conn, metadata, doctor.user_id),
                    "consultorio_id": office_id,
                    "fecha_inicio": today,
                    "fecha_fin": None,
                    "activa": True,
                },
            )
            for start, end in _slots(today, days, now):
                slot_id = str(uuid.uuid4())
                _insert(
                    conn,
                    metadata,
                    "horarios_disponibles",
                    {
                        "id": slot_id,
                        "agenda_id": agenda_id,
                        "inicio_utc": start,
                        "fin_utc": end,
                        "estado": "DISPONIBLE",
                    },
                )
                slots.append(Slot(id=slot_id, doctor_local=doctor.local, start=start))
        service_id = _insert_services(conn, metadata)
    return service_id, slots


def _medico_id(conn: Connection, metadata: MetaData, user_id: str) -> str:
    """The clinician profile row that create_user wrote for this account."""
    medicos = metadata.tables["medicos"]
    return conn.execute(select(medicos.c.id).where(medicos.c.usuario_id == user_id)).scalar_one()


def _insert_services(conn: Connection, metadata: MetaData) -> str:
    """Creates the demo services and returns the first one, which every demo booking uses."""
    service_ids: list[str] = []
    for name in SERVICE_NAMES:
        service_id = str(uuid.uuid4())
        _insert(
            conn,
            metadata,
            "servicios_medicos",
            {
                "id": service_id,
                "nombre": name,
                "duracion_min": SLOT_MINUTES,
                "precio_base": Decimal("25.00"),
                "moneda": "USD",
                "activo": True,
            },
        )
        service_ids.append(service_id)
    return service_ids[0]


def _book_slots(
    scheduling: Any,
    engine: Engine,
    patients: list[Account],
    slots: list[Slot],
    service_id: str,
) -> int:
    """One booking per patient through SchedulingService.book, so each writes cita.reservada."""
    if len(slots) < len(patients):
        raise ValueError("not enough slots for the demo patients; increase --days")
    directory = SqlUserDirectory(engine)
    for index, patient in enumerate(patients):
        actor = directory.load_access(patient.user_id)
        if actor is None:
            raise ValueError(f"{patient.email} has no access profile")
        scheduling.book(
            actor,
            idempotency_key=_booking_key(patient, slots[index]),
            horario_id=slots[index].id,
            servicio_id=service_id,
            motivo=None,
        )
    return len(patients)


def _booking_key(patient: Account, slot: Slot) -> str:
    """The same patient, doctor and slot always give the same key, so a repeat is a replay."""
    return f"demo-{patient.local}-{slot.doctor_local}-{slot.start:%Y%m%d%H%M}"


def _slots(first_day: date, days: int, now_utc: datetime) -> list[tuple[datetime, datetime]]:
    """Weekday slots between OPENS and CLOSES in branch time, as naive UTC, after now."""
    slots = []
    step = timedelta(minutes=SLOT_MINUTES)
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = datetime.combine(day, OPENS, BRANCH_TZ)
        close = datetime.combine(day, CLOSES, BRANCH_TZ)
        while start + step <= close:
            if start.astimezone(UTC).replace(tzinfo=None) > now_utc:
                slots.append(
                    (
                        start.astimezone(UTC).replace(tzinfo=None),
                        (start + step).astimezone(UTC).replace(tzinfo=None),
                    )
                )
            start += step
    return slots


def _insert(conn: Connection, metadata: MetaData, table_name: str, values: dict[str, Any]) -> None:
    """Insert one row, refusing unknown columns and missing required ones.

    Server-generated columns (rowversion/timestamp) are skipped; everything else that is NOT
    NULL without a default must be supplied, so a schema change fails the seed loudly.
    """
    table: Table = metadata.tables[table_name]
    unknown = set(values) - set(table.c.keys())
    if unknown:
        raise ValueError(f"{table_name} has no columns {sorted(unknown)}")
    missing = [
        column.name
        for column in table.columns
        if not column.nullable
        and column.name not in values
        and column.server_default is None
        and not column.primary_key
        and "TIMESTAMP" not in str(column.type).upper()
    ]
    if missing:
        raise ValueError(f"{table_name} needs values for {missing}")
    conn.execute(insert(table).values(**values))


if __name__ == "__main__":
    raise SystemExit(main())

"""Demo seed: accounts, agendas and bookable slots for a demonstration database.

Works on any database the schema exists in (SQL Server or PostgreSQL): it reflects the
tables and writes with plain SQLAlchemy Core, so no dialect-specific SQL is used. It never calls
the application's use cases, so no outbox event is written and no replica (Supabase or Mongo)
receives anything.

    $env:AZURE_DATABASE_URL = "mssql+pyodbc://..."   # or a PostgreSQL URL for local tests
    uv run python scripts/seed_demo.py --confirm <exact database name> \
        --credentials-out C:\\secure\\demo-credentials.json

With APP_ENV=production the seed also needs --allow-production, so it never runs by accident.
Refuses to run when --confirm does not match the database name, or when the credentials file
would be written inside this repository. Running it twice adds nothing: accounts already seeded
are detected and skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import MetaData, Table, create_engine, func, insert, select
from sqlalchemy.engine import make_url

from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DOMAIN = "demo.test"
BRANCH_TZ = timezone(timedelta(hours=-5))  # fixed offset, same convention as seed_availability
OPENS = time(8, 0)
CLOSES = time(17, 0)
SLOT_MINUTES = 30
PATIENT_COUNT = 6
DOCTOR_COUNT = 2
SERVICE_NAMES = ("Consulta general", "Control de laboratorio")


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

    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    if "usuarios" not in metadata.tables:
        print("The schema is missing; run the migrations on this database first.", file=sys.stderr)
        return 2

    with engine.connect() as conn:
        already = conn.execute(
            select(func.count())
            .select_from(metadata.tables["usuarios"])
            .where(metadata.tables["usuarios"].c.email.like(f"%@{DEMO_DOMAIN}"))
        ).scalar_one()
    if already:
        print(f"Already seeded: {already} demo accounts present; nothing added.")
        return 0

    outbox_before = _count(engine, metadata, "eventos_outbox")
    faker = Faker("es_MX")
    Faker.seed(args.seed)
    faker.seed_instance(args.seed)
    try:
        report, credentials = _seed(engine, metadata, faker, args.days)
    except ValueError as error:
        print(f"Seed aborted: {error}", file=sys.stderr)
        return 1
    outbox_after = _count(engine, metadata, "eventos_outbox")
    if outbox_before != outbox_after:
        print("Seed aborted: an outbox event was written.", file=sys.stderr)
        return 1

    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.write_text(
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
    print(
        f"Seeded {report['usuarios']} accounts, {report['doctores']} doctors, "
        f"{report['horarios']} slots over {args.days} days. Credentials written outside the repo. "
        "Outbox untouched."
    )
    return 0


def _count(engine, metadata: MetaData, table: str) -> int | None:
    if table not in metadata.tables:
        return None
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(metadata.tables[table])).scalar_one()


def _seed(engine, metadata: MetaData, faker: Faker, days: int) -> tuple[dict[str, int], list[dict]]:
    hasher = Argon2PasswordHasher()
    now = datetime.now(UTC).replace(tzinfo=None)
    today = datetime.now(BRANCH_TZ).date()
    credentials: list[dict[str, Any]] = []
    report = {"usuarios": 0, "doctores": 0, "horarios": 0}

    with engine.begin() as conn:
        roles = {
            row.nombre: row.id
            for row in conn.execute(
                select(metadata.tables["roles"].c.id, metadata.tables["roles"].c.nombre)
            )
        }
        for name in ("PACIENTE", "MEDICO", "ADMINISTRADOR", "LABORATORISTA"):
            if name not in roles:
                raise ValueError(f"role {name} is missing; the reference data is not loaded")

        accounts = [("administrador", "ADMINISTRADOR")]
        accounts += [("laboratorista", "LABORATORISTA")]
        accounts += [(f"medico{n}", "MEDICO") for n in range(1, DOCTOR_COUNT + 1)]
        accounts += [(f"paciente{n}", "PACIENTE") for n in range(1, PATIENT_COUNT + 1)]

        doctor_ids: list[str] = []
        patient_ids: list[str] = []
        for local, role in accounts:
            user_id = str(uuid.uuid4())
            email = f"{local}@{DEMO_DOMAIN}"
            password = secrets.token_urlsafe(12)
            first, last = faker.first_name(), faker.last_name()
            _insert(
                conn,
                metadata,
                "usuarios",
                {
                    "id": user_id,
                    "email": email,
                    "nombre": first,
                    "apellido": last,
                    "telefono": faker.msisdn()[:25],
                    "estado": "ACTIVO",
                    "email_verificado_en": now,
                    "creado_en": now,
                    "actualizado_en": now,
                },
            )
            _insert(
                conn,
                metadata,
                "credenciales_usuario",
                {
                    "usuario_id": user_id,
                    "password_hash": hasher.hash(password),
                    "actualizado_en": now,
                    "intentos_fallidos": 0,
                },
            )
            _insert(
                conn,
                metadata,
                "usuarios_roles",
                {
                    "usuario_id": user_id,
                    "rol_id": roles[role],
                },
            )
            report["usuarios"] += 1
            credentials.append({"rol": role, "email": email, "password": password})

            if role == "MEDICO":
                doctor_id = str(uuid.uuid4())
                _insert(
                    conn,
                    metadata,
                    "medicos",
                    {
                        "id": doctor_id,
                        "usuario_id": user_id,
                        "registro_profesional": f"DEMO-{len(doctor_ids) + 1:04d}",
                        "duracion_cita_min": SLOT_MINUTES,
                        "activo": True,
                    },
                )
                doctor_ids.append(doctor_id)
                report["doctores"] += 1
            if role == "PACIENTE":
                patient_id = str(uuid.uuid4())
                _insert(
                    conn,
                    metadata,
                    "pacientes",
                    {
                        "id": patient_id,
                        "usuario_id": user_id,
                        "fumador": False,
                        "cigarrillos_dia": 0,
                        "creado_en": now,
                        "contacto_emergencia": faker.name()[:150],
                        "telefono_emergencia": faker.msisdn()[:25],
                        "fecha_nacimiento": faker.date_of_birth(minimum_age=18, maximum_age=80),
                    },
                )
                patient_ids.append(patient_id)

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
        for number, doctor_id in enumerate(doctor_ids, start=1):
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
                    "medico_id": doctor_id,
                    "consultorio_id": office_id,
                    "fecha_inicio": today,
                    "fecha_fin": None,
                    "activa": True,
                },
            )
            for start, end in _slots(today, days, now):
                _insert(
                    conn,
                    metadata,
                    "horarios_disponibles",
                    {
                        "id": str(uuid.uuid4()),
                        "agenda_id": agenda_id,
                        "inicio_utc": start,
                        "fin_utc": end,
                        "estado": "DISPONIBLE",
                    },
                )
                report["horarios"] += 1

        for name in SERVICE_NAMES:
            _insert(
                conn,
                metadata,
                "servicios_medicos",
                {
                    "id": str(uuid.uuid4()),
                    "nombre": name,
                    "duracion_min": SLOT_MINUTES,
                    "precio_base": Decimal("25.00"),
                    "moneda": "USD",
                    "activo": True,
                },
            )

    return report, credentials


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


def _insert(conn, metadata: MetaData, table_name: str, values: dict[str, Any]) -> None:
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

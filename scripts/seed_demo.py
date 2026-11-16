"""Demo seed: accounts, profiles, doctor agendas, bookable slots and one booking per demo patient.

Accounts and bookings go through the application services (IdentityService.create_user and
SchedulingService.book), so each account and each booking writes its outbox event in the same
transaction as the data. Sedes, consultorios, agendas, slots and services are plain SQL: no event
exists for them, and the replicated appointment snapshot already carries the slot window.

    $env:AZURE_DATABASE_URL = "mssql+pyodbc://..."
    $env:SQLSERVER_DATABASE = "<the same database name>"
    uv run python scripts/seed_demo.py --confirm <exact database name> \
        --credentials-out C:\\secure\\demo-credentials.json [--reset]

Guards: AZURE_DATABASE_URL is required and must name the database given in --confirm; the
settings database (SQLSERVER_DATABASE) must name the same database; APP_ENV=production needs
--allow-production; the credentials file must be outside this repository. Passwords are random on
every run and are written only to that file.

Rerun: without --reset, an existing demo set is detected and nothing is added. With --reset, the
demo data is deleted first and seeded again with new passwords. The reset (reset_steps) removes
only the @demo.test accounts, the patient and clinician rows they own, and the appointments of
demo patients with their status history and invoices; it frees a slot that a demo booking held.
It writes no outbox event: the sync's hard-delete reconciliation emits the tombstones. It runs in
one transaction, so a row of another account that still references demo data aborts it and
nothing is deleted.

Determinism: on an empty database, the same --seed on the same calendar day gives the same names,
sexes, cedulas, phones, birth dates, emergency contacts, sede, consultorios, services, agendas,
slots and slot times. The ids of the sede, consultorios, services, agendas and slots are derived
(uuid5 of a fixed namespace and a stable key). Not deterministic: the account, patient, doctor,
booking and outbox ids (IdentityService, the repositories and SchedulingService generate them),
and the passwords, which are random by design. Slots start from today, so their times move with
the calendar day.

Profile fields (tipo_documento CEDULA, numero_documento, fecha_nacimiento, sexo_registrado,
contacto_emergencia, telefono_emergencia and usuarios.telefono) are written by plain SQL after the
account is created through the service, because the account service stores only name, email,
password and roles. This is acceptable for demo data. These writes carry no outbox event, so a
replica receives the profile fields only when another path syncs those rows.
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
from sqlalchemy import (
    Connection,
    Engine,
    MetaData,
    Table,
    column,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy import table as sql_table
from sqlalchemy.dialects import mssql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.expression import TableClause
from sqlalchemy.types import String

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.identity.domain.policies import PERM_USERS_ADMIN
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.shared.domain.demographics import SEXES, validate_sex
from medicalbooking.shared.domain.errors import AppError

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DOMAIN = "demo.test"
DEMO_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "medicalbooking-demo-seed")
BRANCH_TZ = timezone(timedelta(hours=-5))  # fixed offset, same convention as seed_availability
OPENS = time(8, 0)
CLOSES = time(17, 0)
SLOT_MINUTES = 30
PATIENT_COUNT = 6
DOCTOR_COUNT = 3
# The laboratory catalog, the same rows an operator sets up in a real environment: specialties,
# and services as (name, duration in minutes, base price in USD, specialty or None, medical order
# required). Services are matched by name, so a rerun converges instead of duplicating them.
SPECIALTIES: tuple[tuple[str, str], ...] = (
    ("Medicina general", "Consultas médicas generales"),
    ("Hematología", "Consultas y exámenes de sangre"),
    ("Bioquímica clínica", "Pruebas de química sanguínea: lípidos y glucosa"),
)
BLOOD_SAMPLE = "Toma de muestra de sangre"
# (name, minutes, price, specialty, requires medical order, dependent service charged with it)
SERVICE_CATALOG: tuple[tuple[str, int, str, str | None, bool, str | None], ...] = (
    ("Consulta médica", 30, "25.00", "Medicina general", False, None),
    ("Consulta de hematología", 30, "30.00", "Hematología", False, None),
    ("Revisión de resultados con el médico", 20, "15.00", None, True, None),
    (BLOOD_SAMPLE, 20, "8.00", None, False, None),
    ("Hemograma completo", 45, "15.00", "Hematología", False, BLOOD_SAMPLE),
    ("Perfil lipídico", 45, "15.00", "Bioquímica clínica", False, BLOOD_SAMPLE),
    ("Glucosa en ayunas", 45, "15.00", "Bioquímica clínica", False, BLOOD_SAMPLE),
)
# The specialty each doctor of the plan practises (by local name).
DOCTOR_SPECIALTY: dict[str, str] = {
    "medico1": "Medicina general",
    "medico2": "Hematología",
    "medico3": "Bioquímica clínica",
}
# Every demo booking uses this service: a medical consultation.
BOOKING_SERVICE = "Consulta médica"
ACCOUNT_PLAN: tuple[tuple[str, str], ...] = (
    ("administrador", "ADMINISTRADOR"),
    ("laboratorista", "LABORATORISTA"),
    *((f"medico{n}", "MEDICO") for n in range(1, DOCTOR_COUNT + 1)),
    *((f"paciente{n}", "PACIENTE") for n in range(1, PATIENT_COUNT + 1)),
)
CEDULA_COEFFICIENTS = (2, 1, 2, 1, 2, 1, 2, 1, 2)
PHONE_PREFIX = "09"  # Ecuadorian mobile numbers: 09 followed by 8 digits
# The seed runs in-process, not as an HTTP caller. This actor is never stored: create_user reads
# only its permissions.
SEED_ACTOR = AccessProfile(
    user_id=str(uuid.UUID(int=0)),
    status="ACTIVO",
    roles=frozenset({"ADMINISTRADOR"}),
    permissions=frozenset({PERM_USERS_ADMIN}),
)

DELETE = "delete"
UPDATE = "update"


@dataclass(frozen=True)
class Account:
    role: str
    local: str
    user_id: str
    email: str
    password: str
    sex: str | None = None
    telefono: str | None = None


@dataclass(frozen=True)
class Slot:
    id: str
    doctor_local: str
    start: datetime  # naive UTC


@dataclass(frozen=True)
class PatientProfile:
    tipo_documento: str
    numero_documento: str
    fecha_nacimiento: date
    sexo_registrado: str
    contacto_emergencia: str
    telefono_emergencia: str


@dataclass(frozen=True)
class ResetStep:
    table: str
    kind: str  # DELETE or UPDATE
    statement: Any

    @property
    def sql(self) -> str:
        """The T-SQL text of the statement, with its literals, for review and tests."""
        compiled = self.statement.compile(
            dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}
        )
        return str(compiled)


def database_name(url: str) -> str:
    """Target database name, from the URL path or from the Database= keyword of odbc_connect."""
    parsed = make_url(url)
    if parsed.database:
        return parsed.database
    odbc = parsed.query.get("odbc_connect", "")
    for part in odbc.split(";"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "database":
            return value.strip()
    return ""


def demo_id(key: str) -> str:
    """A stable id for a demo row: the same key gives the same uuid on every run."""
    return str(uuid.uuid5(DEMO_NAMESPACE, key))


def new_password() -> str:
    """Random on every call, from the operating system; never derived from the seed."""
    return secrets.token_urlsafe(12)


def cedula_check_digit(first_nine: str) -> str:
    """The verifier digit of an Ecuadorian cedula, from its first nine digits."""
    total = 0
    for digit, coefficient in zip(first_nine, CEDULA_COEFFICIENTS, strict=True):
        product = int(digit) * coefficient
        total += product - 9 if product > 9 else product
    return str((10 - total % 10) % 10)


def cedula_is_valid(value: str) -> bool:
    """Ten digits, province 01 to 24, third digit below 6 and a correct verifier digit."""
    return (
        len(value) == 10
        and value.isdigit()
        and 1 <= int(value[:2]) <= 24
        and int(value[2]) < 6
        and cedula_check_digit(value[:9]) == value[9]
    )


def new_cedula(rng: Any, taken: set[str]) -> str:
    """A valid cedula that is not in taken; the returned value is added to taken."""
    while True:
        province = rng.randint(1, 24)
        body = f"{province:02d}{rng.randint(0, 5)}{rng.randrange(10**6):06d}"
        value = body + cedula_check_digit(body)
        if value not in taken:
            taken.add(value)
            return value


def demo_phone(rng: Any) -> str:
    return PHONE_PREFIX + f"{rng.randrange(10**8):08d}"


def patient_profile(faker: Faker, sex: str, taken: set[str]) -> PatientProfile:
    """Profile values drawn from the seeded Faker instance, so a seed gives the same profiles."""
    rng = faker.random
    return PatientProfile(
        tipo_documento="CEDULA",
        numero_documento=new_cedula(rng, taken),
        fecha_nacimiento=faker.date_of_birth(minimum_age=18, maximum_age=75),
        sexo_registrado=validate_sex(sex),
        contacto_emergencia=f"{faker.first_name()} {faker.last_name()}",
        telefono_emergencia=demo_phone(rng),
    )


def _t(name: str, *columns: str) -> TableClause:
    """A bare table description for the reset statements: no reflection and no connection.

    Columns are typed as String only so that literal values render as T-SQL literals.
    """
    return sql_table(name, *(column(c, String) for c in columns))


_USUARIOS = _t("usuarios", "id", "email")
_PACIENTES = _t("pacientes", "id", "usuario_id")
_MEDICOS = _t("medicos", "id", "usuario_id")
_AGENDAS = _t("agendas", "id", "medico_id")
_HORARIOS = _t("horarios_disponibles", "id", "agenda_id", "estado")
_CITAS = _t("citas", "id", "paciente_id", "horario_id")
_FACTURAS = _t("facturas", "id", "cita_id", "paciente_id")
_PAGOS = _t("pagos", "id", "factura_id")
_NOTIFICACIONES = _t("notificaciones", "id", "usuario_id")

# The demo scope. Every reset statement reaches these through the @demo.test account emails.
_DEMO_USERS = select(_USUARIOS.c.id).where(_USUARIOS.c.email.like("%@demo.test"))
_DEMO_PATIENTS = select(_PACIENTES.c.id).where(_PACIENTES.c.usuario_id.in_(_DEMO_USERS))
_DEMO_DOCTORS = select(_MEDICOS.c.id).where(_MEDICOS.c.usuario_id.in_(_DEMO_USERS))
_DEMO_AGENDAS = select(_AGENDAS.c.id).where(_AGENDAS.c.medico_id.in_(_DEMO_DOCTORS))
_DEMO_CITAS = select(_CITAS.c.id).where(_CITAS.c.paciente_id.in_(_DEMO_PATIENTS))
_DEMO_INVOICES = select(_FACTURAS.c.id).where(
    or_(_FACTURAS.c.cita_id.in_(_DEMO_CITAS), _FACTURAS.c.paciente_id.in_(_DEMO_PATIENTS))
)
_DEMO_PAYMENTS = select(_PAGOS.c.id).where(_PAGOS.c.factura_id.in_(_DEMO_INVOICES))
_DEMO_NOTIFICATIONS = select(_NOTIFICACIONES.c.id).where(
    _NOTIFICACIONES.c.usuario_id.in_(_DEMO_USERS)
)


def _delete(table_clause: TableClause, condition: Any) -> ResetStep:
    return ResetStep(table_clause.name, DELETE, delete(table_clause).where(condition))


def reset_steps() -> tuple[ResetStep, ...]:
    """The demo reset, children before parents. Every statement is scoped to @demo.test accounts.

    Only rows that a demo account, demo patient or demo doctor owns are deleted, plus the state of
    a slot that a demo booking held (set back to DISPONIBLE). A row of another account that still
    references one of these makes the database refuse the delete, and the reset rolls back.
    """
    eventos_seguridad = _t("eventos_seguridad", "usuario_id")
    auditoria = _t("auditoria_accesos", "usuario_id", "paciente_id")
    accesos = _t("accesos_paciente", "paciente_id", "medico_id")
    notif_eventos = _t("notificacion_eventos", "notificacion_id")
    notif_intentos = _t("notificacion_intentos", "notificacion_id")
    verificaciones = _t("verificaciones_correo", "usuario_id")
    stripe = _t("stripe_clientes", "usuario_id")
    reembolsos = _t("reembolsos", "pago_id")
    facturas_detalle = _t("facturas_detalle", "factura_id")
    aprobaciones = _t("aprobaciones_cancelacion_medico", "cita_id")
    consultas = _t("consultas_medicas", "cita_id")
    historial = _t("historial_estados_cita", "cita_id")
    medicos_especialidades = _t("medicos_especialidades", "medico_id")
    usuarios_roles = _t("usuarios_roles", "usuario_id")
    credenciales = _t("credenciales_usuario", "usuario_id")
    freed_slots = select(_CITAS.c.horario_id).where(_CITAS.c.id.in_(_DEMO_CITAS))

    return (
        _delete(eventos_seguridad, eventos_seguridad.c.usuario_id.in_(_DEMO_USERS)),
        _delete(
            auditoria,
            or_(
                auditoria.c.usuario_id.in_(_DEMO_USERS),
                auditoria.c.paciente_id.in_(_DEMO_PATIENTS),
            ),
        ),
        _delete(
            accesos,
            or_(accesos.c.paciente_id.in_(_DEMO_PATIENTS), accesos.c.medico_id.in_(_DEMO_DOCTORS)),
        ),
        _delete(notif_eventos, notif_eventos.c.notificacion_id.in_(_DEMO_NOTIFICATIONS)),
        _delete(notif_intentos, notif_intentos.c.notificacion_id.in_(_DEMO_NOTIFICATIONS)),
        _delete(_NOTIFICACIONES, _NOTIFICACIONES.c.usuario_id.in_(_DEMO_USERS)),
        _delete(verificaciones, verificaciones.c.usuario_id.in_(_DEMO_USERS)),
        _delete(stripe, stripe.c.usuario_id.in_(_DEMO_USERS)),
        _delete(reembolsos, reembolsos.c.pago_id.in_(_DEMO_PAYMENTS)),
        _delete(_PAGOS, _PAGOS.c.factura_id.in_(_DEMO_INVOICES)),
        _delete(facturas_detalle, facturas_detalle.c.factura_id.in_(_DEMO_INVOICES)),
        _delete(_FACTURAS, _FACTURAS.c.id.in_(_DEMO_INVOICES)),
        _delete(aprobaciones, aprobaciones.c.cita_id.in_(_DEMO_CITAS)),
        _delete(consultas, consultas.c.cita_id.in_(_DEMO_CITAS)),
        _delete(historial, historial.c.cita_id.in_(_DEMO_CITAS)),
        ResetStep(
            _HORARIOS.name,
            UPDATE,
            update(_HORARIOS)
            .where(_HORARIOS.c.id.in_(freed_slots), _HORARIOS.c.estado == "RESERVADO")
            .values(estado="DISPONIBLE"),
        ),
        _delete(_CITAS, _CITAS.c.id.in_(_DEMO_CITAS)),
        _delete(_HORARIOS, _HORARIOS.c.agenda_id.in_(_DEMO_AGENDAS)),
        _delete(_AGENDAS, _AGENDAS.c.id.in_(_DEMO_AGENDAS)),
        _delete(medicos_especialidades, medicos_especialidades.c.medico_id.in_(_DEMO_DOCTORS)),
        _delete(_MEDICOS, _MEDICOS.c.id.in_(_DEMO_DOCTORS)),
        _delete(_PACIENTES, _PACIENTES.c.id.in_(_DEMO_PATIENTS)),
        _delete(usuarios_roles, usuarios_roles.c.usuario_id.in_(_DEMO_USERS)),
        _delete(credenciales, credenciales.c.usuario_id.in_(_DEMO_USERS)),
        _delete(_USUARIOS, _USUARIOS.c.id.in_(_DEMO_USERS)),
    )


def reset_demo_rows(engine: Engine) -> dict[str, int]:
    """Run reset_steps in one transaction; returns the rows deleted per table and slots freed."""
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for step in reset_steps():
            affected = conn.execute(step.statement).rowcount
            key = step.table if step.kind == DELETE else "horarios_liberados"
            counts[key] = counts.get(key, 0) + affected
    return counts


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the existing demo data first, then seed again with new passwords",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    url = os.environ.get("AZURE_DATABASE_URL")
    if not url:
        print("Set AZURE_DATABASE_URL to the target database.", file=sys.stderr)
        return 2
    if os.environ.get("APP_ENV", "").lower() == "production" and not args.allow_production:
        print("APP_ENV=production: pass --allow-production to seed demo accounts.", file=sys.stderr)
        return 2
    database = database_name(url)
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
    if already and not args.reset:
        print(
            f"Already seeded: {already} demo accounts present; nothing added. "
            "Pass --reset to delete them and seed again."
        )
        return 0
    if args.reset and already:
        try:
            removed = reset_demo_rows(engine)
        except IntegrityError:
            print(
                "Reset aborted: a demo row is still referenced by data outside the demo set. "
                "Nothing was deleted.",
                file=sys.stderr,
            )
            return 1
        print("Reset removed: " + ", ".join(f"{t} {n}" for t, n in removed.items()) + ".")

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
    _write_profiles(engine, accounts, faker)
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
        password = new_password()
        sex = faker.random.choice(SEXES)
        first_name = faker.first_name_male() if sex == "M" else faker.first_name_female()
        user_id = identity.create_user(
            SEED_ACTOR,
            email=email,
            password=password,
            first_name=first_name,
            last_name=faker.last_name(),
            roles=[role],
            professional_registration=f"DEMO-{local.upper()}" if role == "MEDICO" else None,
        )
        accounts.append(
            Account(
                role=role,
                local=local,
                user_id=user_id,
                email=email,
                password=password,
                sex=sex,
                telefono=demo_phone(faker.random),
            )
        )
    return accounts


def _write_profiles(engine: Engine, accounts: list[Account], faker: Faker) -> None:
    """Plain SQL, no outbox event: the account service stores no profile fields (module docstring).

    Cedulas already held by patients outside this set are skipped, so the unique index on
    (tipo_documento, numero_documento) cannot fail on a value this run chose.
    """
    with engine.begin() as conn:
        taken = set(
            conn.execute(
                text(
                    "SELECT numero_documento FROM pacientes "
                    "WHERE tipo_documento = 'CEDULA' AND numero_documento IS NOT NULL"
                )
            ).scalars()
        )
        for account in accounts:
            conn.execute(
                text("UPDATE usuarios SET telefono = :telefono WHERE id = :usuario"),
                {"telefono": account.telefono, "usuario": account.user_id},
            )
            if account.role != "PACIENTE":
                continue
            profile = patient_profile(faker, account.sex or "", taken)
            conn.execute(
                text(
                    "UPDATE pacientes SET tipo_documento = :tipo, numero_documento = :numero, "
                    "fecha_nacimiento = :nacimiento, sexo_registrado = :sexo, "
                    "contacto_emergencia = :contacto, telefono_emergencia = :telefono "
                    "WHERE usuario_id = :usuario"
                ),
                {
                    "tipo": profile.tipo_documento,
                    "numero": profile.numero_documento,
                    "nacimiento": profile.fecha_nacimiento,
                    "sexo": profile.sexo_registrado,
                    "contacto": profile.contacto_emergencia,
                    "telefono": profile.telefono_emergencia,
                    "usuario": account.user_id,
                },
            )


def _seed_schedule(
    engine: Engine, metadata: MetaData, doctors: list[Account], days: int
) -> tuple[str, list[Slot]]:
    """Plain SQL: sede, consultorios, agendas, slots and services have no outbox event.

    Ids are derived from stable keys. The shared rows (sede, consultorios, services) are inserted
    only when missing, so a reseed after a reset reuses them.
    """
    today = datetime.now(BRANCH_TZ).date()
    now = datetime.now(UTC).replace(tzinfo=None)
    slots: list[Slot] = []
    with engine.begin() as conn:
        sede_id = demo_id("sede:principal")
        _insert_if_missing(
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
            office_id = demo_id(f"consultorio:{number}")
            _insert_if_missing(
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
            agenda_id = demo_id(f"agenda:{doctor.local}")
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
                slot_id = demo_id(f"horario:{doctor.local}:{start:%Y%m%d%H%M}")
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
        specialty_ids = _ensure_specialties(conn, metadata)
        _link_doctor_specialties(conn, metadata, doctors, specialty_ids)
        service_id = _insert_services(conn, metadata, specialty_ids)
    return service_id, slots


def _medico_id(conn: Connection, metadata: MetaData, user_id: str) -> str:
    """The clinician profile row that create_user wrote for this account."""
    medicos = metadata.tables["medicos"]
    return conn.execute(select(medicos.c.id).where(medicos.c.usuario_id == user_id)).scalar_one()


def _ensure_specialties(conn: Connection, metadata: MetaData) -> dict[str, int]:
    """Creates the specialties missing by name (identity ids) and returns their ids by name."""
    table = metadata.tables["especialidades"]
    for name, description in SPECIALTIES:
        if conn.execute(select(table.c.id).where(table.c.nombre == name)).first() is None:
            conn.execute(insert(table).values(nombre=name, descripcion=description, activo=True))
    return {
        name: conn.execute(select(table.c.id).where(table.c.nombre == name)).scalar_one()
        for name, _ in SPECIALTIES
    }


def _link_doctor_specialties(
    conn: Connection, metadata: MetaData, doctors: list[Account], specialty_ids: dict[str, int]
) -> None:
    """Each doctor of the plan gets its specialty; a link that already exists is left alone."""
    links = metadata.tables["medicos_especialidades"]
    for doctor in doctors:
        name = DOCTOR_SPECIALTY.get(doctor.local)
        if name is None:
            continue
        medico_id = _medico_id(conn, metadata, doctor.user_id)
        present = conn.execute(
            select(links.c.medico_id).where(
                links.c.medico_id == medico_id, links.c.especialidad_id == specialty_ids[name]
            )
        ).first()
        if present is None:
            conn.execute(
                insert(links).values(medico_id=medico_id, especialidad_id=specialty_ids[name])
            )


def _insert_services(conn: Connection, metadata: MetaData, specialty_ids: dict[str, int]) -> str:
    """Sets the catalog services and returns the id of the one every demo booking uses.

    A service is matched by name: an existing one is brought to the catalog values, a missing one
    is inserted with a derived id.
    """
    services = metadata.tables["servicios_medicos"]
    ids: dict[str, str] = {}
    for name, minutes, price, specialty, requires_order, _dependency in SERVICE_CATALOG:
        values = {
            "nombre": name,
            "duracion_min": minutes,
            "precio_base": Decimal(price),
            "moneda": "USD",
            "activo": True,
            "requiere_orden_medica": requires_order,
            "especialidad_id": specialty_ids[specialty] if specialty else None,
        }
        existing = conn.execute(select(services.c.id).where(services.c.nombre == name)).scalar()
        if existing is None:
            service_id = demo_id(f"servicio:{name}")
            _insert(conn, metadata, "servicios_medicos", {"id": service_id, **values})
        else:
            service_id = str(existing)
            conn.execute(update(services).where(services.c.id == existing).values(**values))
        ids[name] = service_id
    # Dependencies are set once every service has its id: a dependent points at its sample.
    for name, _minutes, _price, _specialty, _requires_order, dependency in SERVICE_CATALOG:
        conn.execute(
            update(services)
            .where(services.c.id == ids[name])
            .values(servicio_adicional_id=ids[dependency] if dependency else None)
        )
    if BOOKING_SERVICE not in ids:
        raise ValueError(f"the catalog has no {BOOKING_SERVICE!r} service")
    return ids[BOOKING_SERVICE]


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


def _insert_if_missing(
    conn: Connection, metadata: MetaData, table_name: str, values: dict[str, Any]
) -> None:
    """Insert unless a row with this id already exists (shared demo rows, derived ids)."""
    table: Table = metadata.tables[table_name]
    if conn.execute(select(table.c.id).where(table.c.id == values["id"])).first() is None:
        _insert(conn, metadata, table_name, values)


if __name__ == "__main__":
    raise SystemExit(main())

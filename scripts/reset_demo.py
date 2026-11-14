"""Remove the demo data only. Destructive: run it only against a database you mean to reset.

    uv run python scripts/reset_demo.py --confirm <database name> [--allow-production]

Demo data: every account whose email ends with @demo.test, its patient profile, its roles and
credentials, and the appointments of that patient (with their status history). A deleted
appointment frees its slot (RESERVADO back to DISPONIBLE), as the cancel path does. Each account is
removed in one SQL Server transaction together with the outbox events (usuario.eliminado,
cita.eliminada) that tell the replicas to delete it. An account that another row still references
is left whole and emits nothing. A second run finds nothing and emits nothing.

Refuses to run when APP_ENV=production unless --allow-production is given, and refuses unless
--confirm equals the configured database name. Only ids are printed, never emails, names or
payload values.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.config import Settings
from medicalbooking.shared.infrastructure.database import create_database_engine
from medicalbooking.shared.infrastructure.outbox import append_outbox

DEMO_EMAIL_PATTERN = "%@demo.test"

# Runs after the account's appointments are gone: the patient profile goes before the account.
_ACCOUNT_STATEMENTS = (
    "DELETE FROM pacientes WHERE usuario_id = :u",
    "DELETE FROM usuarios_roles WHERE usuario_id = :u",
    "DELETE FROM credenciales_usuario WHERE usuario_id = :u",
    "DELETE FROM usuarios WHERE id = :u",
)


@dataclass
class ResetResult:
    accounts: list[str] = field(default_factory=list)
    appointments: int = 0
    events: int = 0
    failed: list[str] = field(default_factory=list)


def reset_demo(engine: Engine, now: datetime) -> ResetResult:
    """Remove each demo account in its own transaction; keep the ids of the ones left whole."""
    result = ResetResult()
    with engine.connect() as conn:
        account_ids = (
            conn.execute(
                text(
                    "SELECT LOWER(CAST(id AS varchar(36))) FROM usuarios "
                    "WHERE LOWER(email) LIKE :p ORDER BY id"
                ),
                {"p": DEMO_EMAIL_PATTERN},
            )
            .scalars()
            .all()
        )
    for account_id in account_ids:
        try:
            with engine.begin() as tx:
                appointments = _remove_account(tx, account_id, now)
        except IntegrityError:
            result.failed.append(account_id)
            continue
        result.accounts.append(account_id)
        result.appointments += appointments
        result.events += appointments + 1
    return result


def _remove_account(conn: Connection, account_id: str, now: datetime) -> int:
    """Remove one account, its appointments and the events for both, in the caller's transaction."""
    appointments = conn.execute(
        text(
            "SELECT LOWER(CAST(c.id AS varchar(36))), CAST(c.horario_id AS varchar(36)) "
            "FROM citas c JOIN pacientes p ON p.id = c.paciente_id WHERE p.usuario_id = :u"
        ),
        {"u": account_id},
    ).all()
    for appointment_id, horario_id in appointments:
        _remove_appointment(conn, appointment_id, horario_id, now)
    for statement in _ACCOUNT_STATEMENTS:
        conn.execute(text(statement), {"u": account_id})
    append_outbox(
        conn, "usuario", account_id, "usuario.eliminado", {"usuario": {"id": account_id}}, now
    )
    return len(appointments)


def _remove_appointment(
    conn: Connection, appointment_id: str, horario_id: str, now: datetime
) -> None:
    """Delete one appointment, free its slot as the cancel path does, and emit the delete event."""
    conn.execute(
        text("DELETE FROM historial_estados_cita WHERE cita_id = :c"), {"c": appointment_id}
    )
    conn.execute(text("DELETE FROM citas WHERE id = :c"), {"c": appointment_id})
    conn.execute(
        text(
            "UPDATE horarios_disponibles SET estado = 'DISPONIBLE' "
            "WHERE id = :h AND estado = 'RESERVADO'"
        ),
        {"h": horario_id},
    )
    append_outbox(
        conn, "cita", appointment_id, "cita.eliminada", {"cita": {"id": appointment_id}}, now
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--confirm", required=True, help="the configured database name, typed again"
    )
    parser.add_argument(
        "--allow-production", action="store_true", help="required when APP_ENV is production"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.app_env == "production" and not args.allow_production:
        print(
            "Refusing to reset demo data in production without --allow-production.",
            file=sys.stderr,
        )
        return 2
    if args.confirm != settings.sqlserver_database:
        print("--confirm does not match the configured database name.", file=sys.stderr)
        return 2

    engine = create_database_engine(settings)
    try:
        result = reset_demo(engine, datetime.now(UTC).replace(tzinfo=None))
    finally:
        engine.dispose()
    for account_id in result.accounts:
        print(f"removed demo account {account_id}")
    for account_id in result.failed:
        print(f"left whole, still referenced: demo account {account_id}", file=sys.stderr)
    print(
        f"accounts={len(result.accounts)} appointments={result.appointments} "
        f"events={result.events} failed={len(result.failed)}"
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

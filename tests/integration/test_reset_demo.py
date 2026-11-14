"""Demo reset: only @demo.test accounts and what they own are removed (SQL Server).

Runs against the migrated test database (MEDICALBOOKING_TEST_DATABASE). The reset is called
with that engine directly. main() is exercised only for its refusals, which happen before any
connection is opened, so no real database is ever reached from this file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.identity.application.ports import NewUser
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher
from tests.integration.test_scheduling_api import _service, _slot, _user

_SPEC = importlib.util.spec_from_file_location(
    "reset_demo", Path(__file__).parents[2] / "scripts" / "reset_demo.py"
)
reset_demo = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = reset_demo  # dataclasses look the module up by name
_SPEC.loader.exec_module(reset_demo)

pytestmark = pytest.mark.integration
PASSWORD = "Clave-de-prueba-123"  # noqa: S105 - test-only password hashed into the test database


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _demo_account(engine: Engine) -> str:
    user_id = str(uuid.uuid4())
    SqlUserDirectory(engine).create_user(
        NewUser(
            user_id=user_id,
            email=f"paciente-{user_id}@demo.test",
            first_name="Demo",
            last_name="Prueba",
            password_hash=Argon2PasswordHasher().hash(PASSWORD),
            roles=("PACIENTE",),
        )
    )
    return user_id


def _patient_of(engine: Engine, user_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT CAST(id AS varchar(36)) FROM pacientes WHERE usuario_id = :u"),
                {"u": user_id},
            ).scalar_one()
        )


def _cita_of(engine: Engine, patient_id: str) -> str:
    """One appointment of the patient, booked the way the API books: its slot is RESERVADO."""
    horario_id = _slot(engine, days_ahead=5)
    servicio_id = _service(engine)
    cita_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE horarios_disponibles SET estado = 'RESERVADO' "
                "WHERE id = :h AND estado = 'DISPONIBLE'"
            ),
            {"h": horario_id},
        )
        conn.execute(
            text(
                "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, "
                "reservada_en, clave_idempotencia) "
                "VALUES (:c, :p, :h, :s, 'PENDIENTE_PAGO', SYSUTCDATETIME(), :k)"
            ),
            {
                "c": cita_id,
                "p": patient_id,
                "h": horario_id,
                "s": servicio_id,
                "k": f"reset-{uuid.uuid4()}",
            },
        )
        conn.execute(
            text(
                "INSERT INTO historial_estados_cita (id, cita_id, usuario_id, estado_anterior, "
                "estado_nuevo, cambiado_en) "
                "VALUES (NEWID(), :c, NULL, NULL, 'PENDIENTE_PAGO', SYSUTCDATETIME())"
            ),
            {"c": cita_id},
        )
    return cita_id


def _count(engine: Engine, sql: str, **params: Any) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def _owned_rows(engine: Engine, user_id: str) -> dict[str, int]:
    """How many rows of the account and its profile still exist."""
    return {
        "usuarios": _count(engine, "SELECT COUNT(*) FROM usuarios WHERE id = :u", u=user_id),
        "credenciales": _count(
            engine, "SELECT COUNT(*) FROM credenciales_usuario WHERE usuario_id = :u", u=user_id
        ),
        "roles": _count(
            engine, "SELECT COUNT(*) FROM usuarios_roles WHERE usuario_id = :u", u=user_id
        ),
        "pacientes": _count(
            engine, "SELECT COUNT(*) FROM pacientes WHERE usuario_id = :u", u=user_id
        ),
    }


def _cita_rows(engine: Engine, cita_id: str) -> int:
    return _count(
        engine,
        "SELECT (SELECT COUNT(*) FROM citas WHERE id = :c) "
        "+ (SELECT COUNT(*) FROM historial_estados_cita WHERE cita_id = :c)",
        c=cita_id,
    )


def _horario_of(engine: Engine, cita_id: str) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT CAST(horario_id AS varchar(36)) FROM citas WHERE id = :c"),
                {"c": cita_id},
            ).scalar_one()
        )


def _estado_horario(engine: Engine, horario_id: str) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT estado FROM horarios_disponibles WHERE id = :h"), {"h": horario_id}
        ).scalar_one()


def _set_estado_horario(engine: Engine, horario_id: str, estado: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE horarios_disponibles SET estado = :e WHERE id = :h"),
            {"e": estado, "h": horario_id},
        )


def _outbox_total(engine: Engine) -> int:
    return _count(engine, "SELECT COUNT(*) FROM eventos_outbox")


def _outbox_for(engine: Engine, aggregate_type: str, aggregate_id: str) -> list[tuple[str, dict]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tipo_evento, payload FROM eventos_outbox "
                "WHERE agregado_tipo = :t AND agregado_id = :a ORDER BY version_agregado"
            ),
            {"t": aggregate_type, "a": aggregate_id},
        ).all()
    return [(row.tipo_evento, json.loads(row.payload)) for row in rows]


def _purge(engine: Engine, user_ids: list[str], cita_ids: list[str]) -> None:
    """Remove what this file created, children first. Outbox events of those ids go too."""
    with engine.begin() as conn:
        for cita_id in cita_ids:
            conn.execute(
                text("DELETE FROM historial_estados_cita WHERE cita_id = :c"), {"c": cita_id}
            )
            conn.execute(text("DELETE FROM citas WHERE id = :c"), {"c": cita_id})
            conn.execute(text("DELETE FROM eventos_outbox WHERE agregado_id = :a"), {"a": cita_id})
        for user_id in user_ids:
            conn.execute(
                text("DELETE FROM eventos_seguridad WHERE usuario_id = :u"), {"u": user_id}
            )
            conn.execute(text("DELETE FROM pacientes WHERE usuario_id = :u"), {"u": user_id})
            conn.execute(text("DELETE FROM usuarios_roles WHERE usuario_id = :u"), {"u": user_id})
            conn.execute(
                text("DELETE FROM credenciales_usuario WHERE usuario_id = :u"), {"u": user_id}
            )
            conn.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": user_id})
            conn.execute(text("DELETE FROM eventos_outbox WHERE agregado_id = :a"), {"a": user_id})


@pytest.fixture
def created(migrated_engine: Engine) -> Iterator[dict[str, list[str]]]:
    """Ids this file created in the test database; they are purged after each test."""
    ids: dict[str, list[str]] = {"users": [], "citas": []}
    yield ids
    _purge(migrated_engine, ids["users"], ids["citas"])


def test_the_demo_account_is_removed_with_its_profile_and_appointments_only(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    bystander = _user(migrated_engine, "PACIENTE")
    created["users"].append(bystander)
    bystander_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, bystander))
    created["citas"].append(bystander_cita)

    result = reset_demo.reset_demo(migrated_engine, _now())

    assert demo in result.accounts
    assert _owned_rows(migrated_engine, demo) == {
        "usuarios": 0,
        "credenciales": 0,
        "roles": 0,
        "pacientes": 0,
    }
    assert _cita_rows(migrated_engine, demo_cita) == 0
    assert _owned_rows(migrated_engine, bystander) == {
        "usuarios": 1,
        "credenciales": 1,
        "roles": 1,
        "pacientes": 1,
    }
    assert _cita_rows(migrated_engine, bystander_cita) == 2
    bystander_horario = _horario_of(migrated_engine, bystander_cita)
    assert _estado_horario(migrated_engine, bystander_horario) == "RESERVADO"


def test_the_reset_writes_the_delete_events_for_the_account_and_its_appointments(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    account_events = _outbox_for(migrated_engine, "usuario", demo)

    reset_demo.reset_demo(migrated_engine, _now())

    assert _outbox_for(migrated_engine, "cita", demo_cita) == [
        ("cita.eliminada", {"cita": {"id": demo_cita}})
    ]
    assert _outbox_for(migrated_engine, "usuario", demo) == account_events + [
        ("usuario.eliminado", {"usuario": {"id": demo}})
    ]


def test_a_second_run_removes_and_emits_nothing(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    created["citas"].append(_cita_of(migrated_engine, _patient_of(migrated_engine, demo)))
    reset_demo.reset_demo(migrated_engine, _now())
    events_before = _outbox_total(migrated_engine)

    second = reset_demo.reset_demo(migrated_engine, _now())

    assert second.accounts == []
    assert second.appointments == 0
    assert second.events == 0
    assert second.failed == []
    assert _outbox_total(migrated_engine) == events_before


def test_an_account_that_cannot_be_removed_is_left_whole_and_emits_nothing(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    demo_horario = _horario_of(migrated_engine, demo_cita)
    events_before = (
        _outbox_for(migrated_engine, "usuario", demo),
        _outbox_for(migrated_engine, "cita", demo_cita),
    )
    # A security event that references the account blocks its delete (foreign key).
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eventos_seguridad (id, usuario_id, tipo_evento, severidad, "
                "resultado, ocurrido_en) VALUES (NEWID(), :u, 'PRUEBA', 'INFO', 'OK', "
                "SYSUTCDATETIME())"
            ),
            {"u": demo},
        )

    result = reset_demo.reset_demo(migrated_engine, _now())

    assert result.failed == [demo]
    assert demo not in result.accounts
    assert _owned_rows(migrated_engine, demo) == {
        "usuarios": 1,
        "credenciales": 1,
        "roles": 1,
        "pacientes": 1,
    }
    assert _cita_rows(migrated_engine, demo_cita) == 2
    assert _estado_horario(migrated_engine, demo_horario) == "RESERVADO"
    assert _outbox_for(migrated_engine, "usuario", demo) == events_before[0]
    assert _outbox_for(migrated_engine, "cita", demo_cita) == events_before[1]


def test_a_demo_appointment_releases_its_slot(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    horario = _horario_of(migrated_engine, demo_cita)
    assert _estado_horario(migrated_engine, horario) == "RESERVADO"

    reset_demo.reset_demo(migrated_engine, _now())

    assert _estado_horario(migrated_engine, horario) == "DISPONIBLE"


def test_a_slot_that_is_not_reserved_keeps_its_state(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    horario = _horario_of(migrated_engine, demo_cita)
    _set_estado_horario(migrated_engine, horario, "BLOQUEADO")

    reset_demo.reset_demo(migrated_engine, _now())

    assert _estado_horario(migrated_engine, horario) == "BLOQUEADO"


def test_the_status_history_of_demo_appointments_goes_and_a_bystander_keeps_its_own(
    migrated_engine: Engine, created: dict[str, list[str]]
) -> None:
    demo = _demo_account(migrated_engine)
    created["users"].append(demo)
    demo_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, demo))
    created["citas"].append(demo_cita)
    bystander = _user(migrated_engine, "PACIENTE")
    created["users"].append(bystander)
    bystander_cita = _cita_of(migrated_engine, _patient_of(migrated_engine, bystander))
    created["citas"].append(bystander_cita)
    history = "SELECT COUNT(*) FROM historial_estados_cita WHERE cita_id = :c"

    reset_demo.reset_demo(migrated_engine, _now())

    assert _count(migrated_engine, history, c=demo_cita) == 0
    assert _count(migrated_engine, history, c=bystander_cita) == 1


def _settings(app_env: str, database: str) -> SimpleNamespace:
    """Settings stand-in: the guards are checked without reading the environment or .env."""
    return SimpleNamespace(app_env=app_env, sqlserver_database=database)


def test_main_refuses_production_without_allow_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset_demo, "Settings", lambda: _settings("production", "MedicalBooking"))

    assert reset_demo.main(["--confirm", "MedicalBooking"]) == 2


def test_main_refuses_a_confirm_that_is_not_the_database_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reset_demo, "Settings", lambda: _settings("local", "MedicalBooking_test"))

    assert reset_demo.main(["--confirm", "MedicalBooking"]) == 2

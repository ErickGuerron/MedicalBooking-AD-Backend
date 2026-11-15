"""The demo reset plan: only @demo.test data, children before parents, no outbox writes.

The plan is pure data (no connection is opened), so these checks run without a database.
The FK edges below are copied from migrations/sql (0001, 0003, 0008, 0013, 0020, 0027) and
migrations/versions/0029, restricted to the tables the plan can delete.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"

ALLOWED_TABLES = frozenset(
    {
        "eventos_seguridad",
        "auditoria_accesos",
        "accesos_paciente",
        "notificacion_eventos",
        "notificacion_intentos",
        "notificaciones",
        "verificaciones_correo",
        "stripe_clientes",
        "reembolsos",
        "pagos",
        "facturas_detalle",
        "facturas",
        "aprobaciones_cancelacion_medico",
        "consultas_medicas",
        "historial_estados_cita",
        "horarios_disponibles",
        "citas",
        "agendas",
        "medicos_especialidades",
        "medicos",
        "pacientes",
        "usuarios_roles",
        "credenciales_usuario",
        "usuarios",
    }
)

# (child, parent): the child's rows must be deleted before the parent's.
FK_EDGES = (
    ("eventos_seguridad", "usuarios"),
    ("auditoria_accesos", "usuarios"),
    ("auditoria_accesos", "pacientes"),
    ("accesos_paciente", "pacientes"),
    ("accesos_paciente", "medicos"),
    ("notificaciones", "usuarios"),
    ("notificacion_intentos", "notificaciones"),
    ("notificacion_eventos", "notificaciones"),
    ("notificacion_eventos", "notificacion_intentos"),
    ("verificaciones_correo", "usuarios"),
    ("stripe_clientes", "usuarios"),
    ("pagos", "facturas"),
    ("reembolsos", "pagos"),
    ("facturas_detalle", "facturas"),
    ("facturas", "citas"),
    ("facturas", "pacientes"),
    ("aprobaciones_cancelacion_medico", "citas"),
    ("consultas_medicas", "citas"),
    ("historial_estados_cita", "citas"),
    ("historial_estados_cita", "usuarios"),
    ("citas", "pacientes"),
    ("citas", "horarios_disponibles"),
    ("horarios_disponibles", "agendas"),
    ("agendas", "medicos"),
    ("medicos_especialidades", "medicos"),
    ("medicos", "usuarios"),
    ("pacientes", "usuarios"),
    ("usuarios_roles", "usuarios"),
    ("credenciales_usuario", "usuarios"),
)


@pytest.fixture(scope="module")
def seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_statement_is_scoped_to_demo_accounts(seed) -> None:
    for step in seed.reset_steps():
        assert "@demo.test" in step.sql, step.table


def test_the_plan_never_writes_the_outbox_or_a_reference_table(seed) -> None:
    steps = seed.reset_steps()

    assert all("eventos_outbox" not in step.sql for step in steps)
    assert {step.table for step in steps} <= ALLOWED_TABLES


def test_only_freed_slot_states_are_updated_and_nothing_else(seed) -> None:
    updates = [step for step in seed.reset_steps() if step.kind == "update"]

    assert [step.table for step in updates] == ["horarios_disponibles"]
    assert "DISPONIBLE" in updates[0].sql
    assert "RESERVADO" in updates[0].sql


def test_children_are_deleted_before_their_parents(seed) -> None:
    deletes = [step.table for step in seed.reset_steps() if step.kind == "delete"]
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for index, table in enumerate(deletes):
        first.setdefault(table, index)
        last[table] = index

    for child, parent in FK_EDGES:
        if child in first and parent in first:
            assert last[child] < first[parent], f"{child} must go before {parent}"


def test_the_account_rows_are_the_last_deletes(seed) -> None:
    deletes = [step.table for step in seed.reset_steps() if step.kind == "delete"]

    assert deletes[-1] == "usuarios"
    assert "usuarios" not in deletes[:-1]


def test_steps_are_kind_delete_or_update_only(seed) -> None:
    assert {step.kind for step in seed.reset_steps()} == {"delete", "update"}

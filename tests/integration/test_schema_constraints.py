"""Database-level guarantees verified against real SQL Server.

These tests prove that the constraints from the reviewed baseline reject invalid
data even when application code is bypassed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.shared.infrastructure.tsql import split_batches

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def _id() -> str:
    return str(uuid.uuid4())


def _insert(conn: Connection, sql: str, **params: object) -> None:
    conn.execute(text(sql), params)


def _user(conn: Connection, user_id: str) -> None:
    _insert(
        conn,
        "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
        "VALUES (:id, :email, N'Nombre', N'Apellido', SYSUTCDATETIME(), SYSUTCDATETIME())",
        id=user_id,
        email=f"{user_id}@example.test",
    )


def _schedule_slot(conn: Connection) -> dict[str, str]:
    """Create a doctor, room, agenda and one slot. Returns the ids used."""
    doctor_user, doctor, sede, consultorio, agenda, slot = (_id() for _ in range(6))
    _user(conn, doctor_user)
    _insert(
        conn,
        "INSERT INTO medicos (id, usuario_id, registro_profesional) VALUES (:id, :u, :r)",
        id=doctor,
        u=doctor_user,
        r=f"REG-{doctor}",
    )
    _insert(conn, "INSERT INTO sedes (id, nombre) VALUES (:id, N'Sede prueba')", id=sede)
    _insert(
        conn,
        "INSERT INTO consultorios (id, sede_id, nombre) VALUES (:id, :s, N'C1')",
        id=consultorio,
        s=sede,
    )
    _insert(
        conn,
        "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
        "VALUES (:id, :m, :c, '2026-11-02')",
        id=agenda,
        m=doctor,
        c=consultorio,
    )
    _insert(
        conn,
        "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
        "VALUES (:id, :a, '2026-11-02T09:00:00', '2026-11-02T09:30:00')",
        id=slot,
        a=agenda,
    )
    return {"slot": slot}


def _patient_and_service(conn: Connection) -> dict[str, str]:
    patient_user, patient, service = _id(), _id(), _id()
    _user(conn, patient_user)
    _insert(
        conn,
        "INSERT INTO pacientes (id, usuario_id, creado_en) VALUES (:id, :u, SYSUTCDATETIME())",
        id=patient,
        u=patient_user,
    )
    _insert(
        conn,
        "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base) "
        "VALUES (:id, N'Consulta', 30, 25.00)",
        id=service,
    )
    return {"patient": patient, "service": service}


def _appointment(conn: Connection, slot: str, patient: str, service: str, status: str) -> None:
    _insert(
        conn,
        "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, reservada_en, "
        "clave_idempotencia) VALUES (:id, :p, :h, :s, :e, SYSUTCDATETIME(), :k)",
        id=_id(),
        p=patient,
        h=slot,
        s=service,
        e=status,
        k=_id(),
    )


def _rejects(conn: Connection, action) -> None:
    """Run a statement that must violate a constraint, keeping the outer transaction usable."""
    with pytest.raises(IntegrityError), conn.begin_nested():
        action()


def test_only_one_active_appointment_per_slot(db: Connection) -> None:
    slot_ids = _schedule_slot(db)
    first = _patient_and_service(db)
    second = _patient_and_service(db)
    _appointment(db, slot_ids["slot"], first["patient"], first["service"], "CONFIRMADA")

    _rejects(
        db,
        lambda: _appointment(
            db, slot_ids["slot"], second["patient"], second["service"], "PENDIENTE_PAGO"
        ),
    )


def test_cancelled_appointment_does_not_block_the_slot(db: Connection) -> None:
    slot_ids = _schedule_slot(db)
    first = _patient_and_service(db)
    second = _patient_and_service(db)
    _appointment(db, slot_ids["slot"], first["patient"], first["service"], "CANCELADA")

    _appointment(db, slot_ids["slot"], second["patient"], second["service"], "PENDIENTE_PAGO")


def test_many_users_may_lack_external_identity(db: Connection) -> None:
    # Regression: a plain UNIQUE on nullable auth_provider_id allowed only one NULL.
    _user(db, _id())
    _user(db, _id())

    count = db.execute(
        text("SELECT COUNT(*) FROM usuarios WHERE auth_provider_id IS NULL")
    ).scalar_one()
    assert count >= 2


def test_slot_end_must_follow_start(db: Connection) -> None:
    agenda_ids = _schedule_slot(db)
    _rejects(
        db,
        lambda: _insert(
            db,
            "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc) "
            "SELECT :id, agenda_id, '2026-11-02T10:00:00', '2026-11-02T10:00:00' "
            "FROM horarios_disponibles WHERE id = :slot",
            id=_id(),
            slot=agenda_ids["slot"],
        ),
    )


def test_invoice_total_must_equal_subtotal_plus_taxes(db: Connection) -> None:
    patient = _patient_and_service(db)
    _rejects(
        db,
        lambda: _insert(
            db,
            "INSERT INTO facturas (id, paciente_id, subtotal, impuestos, total, creada_en) "
            "VALUES (:id, :p, 100.00, 12.00, 100.00, SYSUTCDATETIME())",
            id=_id(),
            p=patient["patient"],
        ),
    )


def test_outbox_payload_must_be_json(db: Connection) -> None:
    _rejects(
        db,
        lambda: _insert(
            db,
            "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, payload, "
            "version_agregado, creado_en) VALUES (:id, 'cita', :a, 'x', 'no-json', 1, "
            "SYSUTCDATETIME())",
            id=_id(),
            a=_id(),
        ),
    )


def test_knowledge_reference_version_must_match_published_row(db: Connection) -> None:
    analyte, source, reference, user = _id(), _id(), _id(), _id()
    _user(db, user)
    _insert(
        db,
        "INSERT INTO conocimiento_analitos (id, nombre, tipo_valor, creado_en, actualizado_en) "
        "VALUES (:id, N'Hemoglobina', 'NUMERICO', SYSUTCDATETIME(), SYSUTCDATETIME())",
        id=analyte,
    )
    _insert(
        db,
        "INSERT INTO conocimiento_fuentes (id, codigo, nombre) VALUES (:id, :c, N'Fuente')",
        id=source,
        c=f"SRC-{source}",
    )
    _insert(
        db,
        "INSERT INTO conocimiento_referencias (id, analito_id, fuente_id, version, unidad, "
        "fecha_inicio, estado) VALUES (:id, :a, :f, 1, 'g/dL', '2026-01-01', 'BORRADOR')",
        id=reference,
        a=analyte,
        f=source,
    )

    _rejects(
        db,
        lambda: _insert(
            db,
            "INSERT INTO resultados_laboratorio (id, detalle_orden_id, nombre_analito, "
            "valor_numerico, referencia_aplicada_id, referencia_version) "
            "VALUES (:id, :d, N'Hemoglobina', 12.5, :r, 2)",
            id=_id(),
            d=_id(),
            r=reference,
        ),
    )


def test_seed_reference_data_is_idempotent(db: Connection) -> None:
    script = (ROOT / "migrations" / "sql" / "0002_seed_reference_data.sql").read_text(
        encoding="utf-8"
    )
    for _ in range(2):
        for batch in split_batches(script):
            db.execute(text(batch))

    roles = db.execute(text("SELECT COUNT(*) FROM roles")).scalar_one()
    assert roles == 4
    # Later migrations add destinations (NOTIFICACIONES), so check the seeded codes are present
    # exactly once each instead of an exact total.
    for code in ("SUPABASE", "MONGODB"):
        count = db.execute(
            text("SELECT COUNT(*) FROM destinos_sincronizacion WHERE codigo = :code"),
            {"code": code},
        ).scalar_one()
        assert count == 1

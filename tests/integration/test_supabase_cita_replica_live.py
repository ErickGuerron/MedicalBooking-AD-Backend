"""Appointments in the Supabase replica obey row-level security for the patient (live).

The writer applies cita.actualizada for a synthetic patient: first the reservation, then a
cancellation carrying a 1500-character doctor reason. The reader, bound to one account through
app.usuario_id, must see that appointment and its exact reason; another patient's account and an
unset session must see nothing. The table must hold only the approved columns, and the writer
must not be able to delete an appointment. Every synthetic row is removed in a finally block.

Runs only when SUPABASE_WRITER_DATABASE_URL, SUPABASE_READER_DATABASE_URL and
SUPABASE_DATABASE_URL are all set.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from medicalbooking.modules.replica.application.projection_consumer import apply_event
from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)

pytestmark = pytest.mark.integration

WRITER = os.environ.get("SUPABASE_WRITER_DATABASE_URL")
READER = os.environ.get("SUPABASE_READER_DATABASE_URL")
OWNER = os.environ.get("SUPABASE_DATABASE_URL")
requires_all = pytest.mark.skipif(
    not (WRITER and READER and OWNER),
    reason="Supabase writer, reader and owner URLs are required",
)
CONSUMER = f"test-{uuid.uuid4().hex[:16]}"  # the column holds 40 characters
REPLICA_COLUMNS = {
    "id",
    "paciente_id",
    "servicio_id",
    "estado",
    "reservada_en",
    "confirmada_en",
    "cancelada_en",
    "motivo_cancelacion_codigo",
    "motivo_cancelacion_texto",
    "inicio",
    "fin",
}


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> dict:
    return {
        "event_id": _id(),
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "aggregate_version": 1,
        "created_at": _now(),
        "payload": payload,
    }


class _Patient:
    """One synthetic patient with one appointment: every id is fresh."""

    def __init__(self) -> None:
        self.user = _id()
        self.paciente = _id()
        self.cita = _id()
        self.servicio = _id()


def _cita(patient: _Patient, estado: str, **reason) -> dict:
    cita = {
        "id": patient.cita,
        "paciente_id": patient.paciente,
        "servicio_id": patient.servicio,
        "estado": estado,
        "reservada_en": _now(),
        "confirmada_en": None,
        "cancelada_en": None,
        "motivo_cancelacion_codigo": None,
        "motivo_cancelacion_texto": None,
        "inicio": "2026-12-01T15:00:00",
        "fin": "2026-12-01T15:30:00",
    }
    cita.update(reason)
    return cita


def _project(sink: SupabaseProjectionSink, patient: _Patient, texto: str) -> None:
    """The account and patient first, then the reservation and its cancellation."""
    apply_event(
        _envelope(
            "usuario.creado",
            "usuario",
            patient.user,
            {
                "usuario": {"id": patient.user, "estado": "ACTIVO"},
                "roles": [1],
                "paciente": {
                    "id": patient.paciente,
                    "usuario_id": patient.user,
                    "nombre": "Sintetica",
                    "apellido": "Paciente",
                },
            },
        ),
        sink,
        CONSUMER,
    )
    apply_event(
        _envelope(
            "cita.actualizada",
            "cita",
            patient.cita,
            {"cita": _cita(patient, "PENDIENTE_PAGO")},
        ),
        sink,
        CONSUMER,
    )
    apply_event(
        _envelope(
            "cita.actualizada",
            "cita",
            patient.cita,
            {
                "cita": _cita(
                    patient,
                    "CANCELADA",
                    cancelada_en=_now(),
                    motivo_cancelacion_codigo="IMPREVISTO_MEDICO",
                    motivo_cancelacion_texto=texto,
                )
            },
        ),
        sink,
        CONSUMER,
    )


def _visible_to(usuario: str | None, cita_id: str) -> dict[str, str | None]:
    """The reader's view of one appointment, bound to one account (or to none)."""
    with psycopg.connect(READER, connect_timeout=15) as conn:
        conn.execute("SELECT set_config('app.usuario_id', %s, true)", (usuario or "",))
        rows = conn.execute(
            "SELECT id, motivo_cancelacion_texto FROM replica.citas WHERE id::text = %s",
            (cita_id,),
        ).fetchall()
    return {str(row[0]): row[1] for row in rows}


def _owner(sql: str, params: tuple = ()) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn:
        conn.execute(sql, params)


def _cleanup(patients: list[_Patient]) -> None:
    for patient in patients:
        _owner("DELETE FROM replica.citas WHERE id = %s", (patient.cita,))
        _owner("DELETE FROM replica.pacientes WHERE id = %s", (patient.paciente,))
        _owner("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (patient.user,))
        _owner("DELETE FROM replica.usuarios WHERE id = %s", (patient.user,))
    _owner("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (CONSUMER,))
    _owner("DELETE FROM replica.watermarks WHERE consumidor = %s", (CONSUMER,))


@requires_all
def test_the_reader_sees_only_its_own_patients_appointment_and_the_writer_stays_least_privileged():
    mine, other = _Patient(), _Patient()
    texto = ("Motivo de cancelacion sintetico. " * 50)[:1500]
    assert len(texto) == 1500
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    try:
        _project(sink, mine, texto)
        _project(sink, other, "Otro motivo sintetico")

        assert _visible_to(mine.user, mine.cita) == {mine.cita: texto}
        assert _visible_to(other.user, mine.cita) == {}
        assert _visible_to(None, mine.cita) == {}

        with psycopg.connect(OWNER, connect_timeout=15) as conn:
            columns = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'replica' AND table_name = 'citas'"
                ).fetchall()
            }
        assert columns == REPLICA_COLUMNS

        with psycopg.connect(WRITER, connect_timeout=15) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM replica.citas WHERE id = %s", (mine.cita,))
    finally:
        _cleanup([mine, other])

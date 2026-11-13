"""Billing in the Supabase replica obeys row-level security for the patient (live).

The writer applies factura.creada, pago.actualizado and reembolso.actualizado for two synthetic
patients. The reader, bound to one account through app.usuario_id, must see only that patient's
invoice, detail line, payment and refund; the other patient's rows and an unset session must see
nothing. The writer must not be able to read the processed-event log or delete invoices. Every
synthetic row is removed in a finally block, even when an assertion fails.

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
    """One synthetic patient with a paid, refunded invoice: every id is fresh."""

    def __init__(self) -> None:
        self.user = _id()
        self.paciente = _id()
        self.factura = _id()
        self.detalle = _id()
        self.pago = _id()
        self.reembolso = _id()


def _project_patient(sink: SupabaseProjectionSink, patient: _Patient) -> None:
    """Apply the events of one patient in dependency order: account, invoice, payment, refund."""
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
    factura = {
        "id": patient.factura,
        "paciente_id": patient.paciente,
        "cita_id": _id(),
        "numero_factura": f"TEST-{patient.factura[:8]}",
        "subtotal": "25.00",
        "impuestos": "0.00",
        "total": "25.00",
        "moneda": "USD",
        "estado": "PENDIENTE",
        "creada_en": _now(),
    }
    detalle = {
        "id": patient.detalle,
        "servicio_id": _id(),
        "descripcion": "Consulta sintetica",
        "cantidad": 1,
        "precio_unitario": "25.00",
        "subtotal": "25.00",
    }
    apply_event(
        _envelope(
            "factura.creada",
            "factura",
            patient.factura,
            {"factura": factura, "detalles": [detalle]},
        ),
        sink,
        CONSUMER,
    )
    pago = {
        "id": patient.pago,
        "factura_id": patient.factura,
        "monto": "25.00",
        "moneda": "USD",
        "estado": "CONFIRMADO",
        "creado_en": _now(),
        "pagado_en": _now(),
    }
    apply_event(
        _envelope(
            "pago.actualizado",
            "pago",
            patient.pago,
            {
                "pago": pago,
                "factura": {
                    "id": patient.factura,
                    "paciente_id": patient.paciente,
                    "estado": "PAGADA",
                },
            },
        ),
        sink,
        CONSUMER,
    )
    reembolso = {
        "id": patient.reembolso,
        "pago_id": patient.pago,
        "monto": "25.00",
        "estado": "PROCESADO",
        "solicitado_en": _now(),
        "procesado_en": _now(),
        "motivo_codigo": "CANCELACION_PACIENTE",
    }
    apply_event(
        _envelope(
            "reembolso.actualizado",
            "reembolso",
            patient.reembolso,
            {
                "reembolso": reembolso,
                "pago": pago,
                "factura": {
                    "id": patient.factura,
                    "paciente_id": patient.paciente,
                    "estado": "REEMBOLSADA",
                },
            },
        ),
        sink,
        CONSUMER,
    )


def _visible_to(usuario: str | None, patient: _Patient) -> dict[str, set[str]]:
    """Read the four billing tables as the reader, bound to one account (or to none)."""
    ids = [patient.factura, patient.detalle, patient.pago, patient.reembolso]
    queries = {
        "facturas": "SELECT id FROM replica.facturas WHERE id::text = ANY(%s)",
        "facturas_detalle": "SELECT id FROM replica.facturas_detalle WHERE id::text = ANY(%s)",
        "pagos": "SELECT id FROM replica.pagos WHERE id::text = ANY(%s)",
        "reembolsos": "SELECT id FROM replica.reembolsos WHERE id::text = ANY(%s)",
    }
    with psycopg.connect(READER, connect_timeout=15) as conn:
        conn.execute("SELECT set_config('app.usuario_id', %s, true)", (usuario or "",))
        seen = {}
        for table, sql in queries.items():
            rows = conn.execute(sql, (ids,)).fetchall()
            seen[table] = {str(row[0]) for row in rows}
    return seen


def _owner(sql: str, params: tuple = ()) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn:
        conn.execute(sql, params)


def _cleanup(patients: list[_Patient]) -> None:
    for patient in patients:
        _owner("DELETE FROM replica.reembolsos WHERE id = %s", (patient.reembolso,))
        _owner("DELETE FROM replica.pagos WHERE id = %s", (patient.pago,))
        _owner("DELETE FROM replica.facturas_detalle WHERE id = %s", (patient.detalle,))
        _owner("DELETE FROM replica.facturas WHERE id = %s", (patient.factura,))
        _owner("DELETE FROM replica.pacientes WHERE id = %s", (patient.paciente,))
        _owner("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (patient.user,))
        _owner("DELETE FROM replica.usuarios WHERE id = %s", (patient.user,))
    _owner("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (CONSUMER,))
    _owner("DELETE FROM replica.watermarks WHERE consumidor = %s", (CONSUMER,))


@requires_all
def test_the_reader_sees_only_its_own_patients_billing_and_the_writer_stays_least_privileged():
    mine, other = _Patient(), _Patient()
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    try:
        _project_patient(sink, mine)
        _project_patient(sink, other)

        own = _visible_to(mine.user, mine)
        assert own == {
            "facturas": {mine.factura},
            "facturas_detalle": {mine.detalle},
            "pagos": {mine.pago},
            "reembolsos": {mine.reembolso},
        }

        theirs = _visible_to(other.user, mine)
        assert all(not rows for rows in theirs.values())

        unset = _visible_to(None, mine)
        assert all(not rows for rows in unset.values())

        with psycopg.connect(WRITER, connect_timeout=15) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE replica.eventos_procesados SET consumidor = consumidor")
        with psycopg.connect(WRITER, connect_timeout=15) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM replica.facturas WHERE id = %s", (mine.factura,))
    finally:
        _cleanup([mine, other])

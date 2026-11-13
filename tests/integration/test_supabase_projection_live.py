"""The consumer's projection writes real rows in the Supabase replica (live, writer role).

The test writes synthetic rows with random ids, reads them back through the owner
connection, and deletes everything in a finally block, even when an assertion fails.
Runs only when both SUPABASE_WRITER_DATABASE_URL and SUPABASE_DATABASE_URL are set.
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
OWNER = os.environ.get("SUPABASE_DATABASE_URL")
requires_both = pytest.mark.skipif(
    not (WRITER and OWNER), reason="Supabase writer and owner URLs are required"
)
CONSUMER = f"test-{uuid.uuid4().hex[:16]}"  # the column holds 40 characters


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "aggregate_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


def _owner_count(sql: str, params: tuple) -> int:
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _cleanup(user: str, patient: str, order: str, detail: str, result: str) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM replica.resultados_laboratorio WHERE id = %s", (result,))
            cur.execute("DELETE FROM replica.ordenes_laboratorio_detalle WHERE id = %s", (detail,))
            cur.execute("DELETE FROM replica.ordenes_laboratorio WHERE id = %s", (order,))
            cur.execute("DELETE FROM replica.pacientes WHERE id = %s", (patient,))
            cur.execute("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (user,))
            cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (user,))
            cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (CONSUMER,))
            cur.execute("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (CONSUMER,))


@requires_both
def test_the_consumer_projects_an_order_and_its_result_and_is_idempotent():
    user, patient, order, detail, result, medico = (str(uuid.uuid4()) for _ in range(6))
    tipo = str(uuid.uuid4())
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    try:
        apply_event(
            _envelope(
                "usuario.creado",
                "usuario",
                user,
                {
                    "usuario": {"id": user, "estado": "ACTIVO"},
                    "roles": [1],
                    "paciente": {
                        "id": patient,
                        "usuario_id": user,
                        "nombre": "Ana",
                        "apellido": "Sintetica",
                    },
                },
            ),
            sink,
            CONSUMER,
        )
        orden = {
            "id": order,
            "paciente_id": patient,
            "medico_id": medico,
            "fecha_orden": datetime.now(UTC).isoformat(),
            "prioridad": "NORMAL",
            "estado": "SOLICITADA",
            "detalles": [{"id": detail, "tipo_prueba_id": tipo, "estado": "PENDIENTE"}],
        }
        creada = _envelope(
            "orden_laboratorio.creada",
            "orden_laboratorio",
            order,
            {"estado": "SOLICITADA", "orden": orden},
        )
        apply_event(creada, sink, CONSUMER)
        resultado = _envelope(
            "resultado_laboratorio.registrado",
            "orden_laboratorio",
            order,
            {
                "detalle_id": detail,
                "estado_orden": "COMPLETADA",
                "orden_estado": "COMPLETADA",
                "detalle": {"id": detail, "estado": "COMPLETADO", "fecha_resultado": None},
                "resultados": [
                    {
                        "id": result,
                        "nombre_analito": "Hemoglobina",
                        "valor_numerico": 14.2,
                        "unidad": "g/dL",
                        "validado_en": datetime.now(UTC).isoformat(),
                    }
                ],
            },
        )
        assert apply_event(resultado, sink, CONSUMER) is True

        assert (
            _owner_count(
                "SELECT COUNT(*) FROM replica.ordenes_laboratorio "
            "WHERE id = %s AND estado = 'COMPLETADA'",
                (order,),
            )
            == 1
        )
        assert (
            _owner_count(
                "SELECT COUNT(*) FROM replica.resultados_laboratorio WHERE detalle_orden_id = %s",
                (detail,),
            )
            == 1
        )
        assert (
            _owner_count(
                "SELECT COUNT(*) FROM replica.pacientes WHERE id = %s AND nombre = 'Ana'",
                (patient,),
            )
            == 1
        )
        assert apply_event(resultado, sink, CONSUMER) is False
    finally:
        _cleanup(user, patient, order, detail, result)

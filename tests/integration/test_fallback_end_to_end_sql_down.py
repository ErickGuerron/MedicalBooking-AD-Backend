"""The patient reads their results while SQL Server is down (spec 8.3, end to end).

The app starts with SQL Server pointed at a closed port, so every SQL path fails. The replica
(Supabase) holds the data, projected with the same consumer code used in production. The
patient's JWT is checked without SQL, the account is checked in the replica, the read is
audited, and the results come back.

The audit sink is in memory: the production audit collection is insert-only and cannot be
cleaned up after a test. The Atlas adapter has its own tests.
Runs only when SUPABASE_WRITER_DATABASE_URL, SUPABASE_READER_DATABASE_URL and
SUPABASE_DATABASE_URL are all set. Every synthetic row is removed in a finally block.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from pydantic import SecretStr

from medicalbooking.app import create_app
from medicalbooking.config import Settings
from medicalbooking.modules.identity.infrastructure.security import JwtTokenIssuer
from medicalbooking.modules.replica.application.fallback_read import (
    AuditRecord,
    FallbackReadService,
)
from medicalbooking.modules.replica.application.projection_consumer import apply_event
from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)
from medicalbooking.modules.replica.infrastructure.supabase_reader import SupabaseReplicaReader

pytestmark = pytest.mark.integration

WRITER = os.environ.get("SUPABASE_WRITER_DATABASE_URL")
READER = os.environ.get("SUPABASE_READER_DATABASE_URL")
OWNER = os.environ.get("SUPABASE_DATABASE_URL")
requires_all = pytest.mark.skipif(
    not (WRITER and READER and OWNER), reason="Supabase writer, reader and owner URLs are required"
)
KEY = "integration-signing-key-with-32+chars"
CONSUMER = f"test-{uuid.uuid4().hex[:16]}"


class MemorySink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _project_patient_with_results(sink, user, patient, order, detail, result, proposal, finding):
    usuario = {"usuario": {"id": str(user), "estado": "ACTIVO"}}
    events = [
        (
            "usuario.creado",
            "usuario",
            str(user),
            {
                **usuario,
                "roles": [1],  # PACIENTE, from the synced catalog
                "paciente": {
                    "id": str(patient),
                    "usuario_id": str(user),
                    "nombre": "Ana",
                    "apellido": "Sintetica",
                },
            },
        ),
        (
            "orden_laboratorio.creada",
            "orden_laboratorio",
            str(order),
            {
                "estado": "SOLICITADA",
                "orden": {
                    "id": str(order),
                    "paciente_id": str(patient),
                    "medico_id": str(uuid.uuid4()),
                    "fecha_orden": _now(),
                    "prioridad": "NORMAL",
                    "estado": "SOLICITADA",
                    "detalles": [
                        {
                            "id": str(detail),
                            "tipo_prueba_id": str(uuid.uuid4()),
                            "estado": "PENDIENTE",
                        }
                    ],
                },
            },
        ),
        (
            "resultado_laboratorio.registrado",
            "orden_laboratorio",
            str(order),
            {
                "detalle_id": str(detail),
                "estado_orden": "COMPLETADA",
                "orden_estado": "COMPLETADA",
                "detalle": {"id": str(detail), "estado": "COMPLETADO", "fecha_resultado": None},
                "resultados": [
                    {
                        "id": str(result),
                        "nombre_analito": "Hemoglobina",
                        "valor_numerico": 14.2,
                        "unidad": "g/dL",
                        "validado_en": _now(),
                    }
                ],
            },
        ),
        (
            "propuesta.revisada",
            "propuesta_clinica",
            str(proposal),
            {
                "propuesta": {
                    "id": str(proposal),
                    "orden_id": str(order),
                    "estado": "ACEPTADA",
                    "resumen_propuesto": "Hemoglobina dentro de rango.",
                    "conclusion_medica": None,
                    "revisado_en": _now(),
                },
                "hallazgos": [
                    {
                        "id": str(finding),
                        "propuesta_id": str(proposal),
                        "tipo": "SIN_EVIDENCIA_DIABETES",
                        "explicacion": "Sin hallazgos.",
                        "regla_version": 1,
                    }
                ],
                "decision": "ACEPTAR",
            },
        ),
    ]
    for event_type, aggregate_type, aggregate_id, payload in events:
        apply_event(
            {
                "event_id": str(uuid.uuid4()),
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "aggregate_version": 1,
                "created_at": _now(),
                "payload": payload,
            },
            sink,
            CONSUMER,
        )


def _cleanup(users, patients, orders, details, results, proposals, findings) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        for table, ids in [
            ("replica.propuestas_hallazgos", findings),
            ("replica.propuestas_clinicas", proposals),
            ("replica.resultados_laboratorio", results),
            ("replica.ordenes_laboratorio_detalle", details),
            ("replica.ordenes_laboratorio", orders),
        ]:
            for value in ids:
                cur.execute(f"DELETE FROM {table} WHERE id = %s", (str(value),))
        for patient in patients:
            cur.execute("DELETE FROM replica.pacientes WHERE id = %s", (str(patient),))
        for user in users:
            cur.execute("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (str(user),))
            cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (str(user),))
        cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (CONSUMER,))
        cur.execute("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (CONSUMER,))


def _app_with_sql_down(service: FallbackReadService):
    settings = Settings(
        _env_file=None,
        app_env="test",
        sqlserver_host="127.0.0.1",
        sqlserver_port=1,  # nothing listens here: every SQL path fails
        jwt_signing_key=SecretStr(KEY),
        api_docs_enabled=False,
    )
    flask_app = create_app(settings)
    flask_app.extensions["medicalbooking.fallback_read"] = service
    return flask_app


@requires_all
def test_a_patient_reads_their_results_while_sql_server_is_down():
    user, other_user = uuid.uuid4(), uuid.uuid4()
    patient, other_patient = uuid.uuid4(), uuid.uuid4()
    order, detail, result = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    proposal, finding = uuid.uuid4(), uuid.uuid4()
    writer = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    audit = MemorySink()
    service = FallbackReadService(
        sink=audit,
        reader=SupabaseReplicaReader(lambda: psycopg.connect(READER, connect_timeout=15)),
        resolver=SupabaseReplicaReader(lambda: psycopg.connect(READER, connect_timeout=15)),
        clock=lambda: datetime.now(UTC),
    )
    try:
        _project_patient_with_results(
            writer, user, patient, order, detail, result, proposal, finding
        )
        app = _app_with_sql_down(service)
        token, _ = JwtTokenIssuer(KEY, 15).issue(str(user))

        response = app.test_client().get(
            "/api/v1/me/resultados-contingencia",
            headers={"Authorization": f"Bearer {token}"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.9"},
        )

        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["paciente_id"] == str(patient)
        assert body["fuente"] == "replica"
        orden = body["ordenes"][0]
        assert orden["id"] == str(order)
        assert orden["resultados"][0]["valor_numerico"] == 14.2
        assert orden["diagnostico"]["conclusion"] == "Hemoglobina dentro de rango."
        assert audit.records[0].paciente_id == str(patient)
        assert audit.records[0].ip_origen == "10.0.0.9"
    finally:
        _cleanup(
            [user, other_user],
            [patient, other_patient],
            [order],
            [detail],
            [result],
            [proposal],
            [finding],
        )

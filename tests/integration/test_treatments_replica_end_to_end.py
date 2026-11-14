"""Treatment events, outbox -> Kafka -> replica consumer -> Supabase, end to end (treatments S11).

The treatment is created, finalized and given a follow-up note, in that order, through the same
Kafka handler and replica consumer the replica uses in production. Each run uses its own topic,
created and deleted by the test. The synthetic rows are removed in a finally block with the owner
connection, because replica_writer has no DELETE privilege on the treatment tables.
Runs only when KAFKA_BOOTSTRAP_SERVERS, SUPABASE_WRITER_DATABASE_URL and SUPABASE_DATABASE_URL
are all set.
"""

import os
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import psycopg
import pytest
from confluent_kafka.admin import AdminClient, NewTopic

from medicalbooking.modules.replica.infrastructure.supabase_projection import (
    SupabaseProjectionSink,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent
from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.modules.synchronization.infrastructure.kafka_producer import (
    build_kafka_producer,
)
from medicalbooking.modules.treatments.application.ports import FollowUp, Treatment
from medicalbooking.modules.treatments.infrastructure.outbox_events import (
    followup_snapshot,
    treatment_snapshot,
)
from medicalbooking.workers.replica_consumer import consume

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
WRITER = os.environ.get("SUPABASE_WRITER_DATABASE_URL")
OWNER = os.environ.get("SUPABASE_DATABASE_URL")
requires_all = pytest.mark.skipif(
    not (BOOTSTRAP and WRITER and OWNER),
    reason="Kafka, Supabase writer and owner URLs are required",
)

DESCRIPCION = "PRUEBA E2E tratamiento sintetico"
NOTA = "PRUEBA E2E seguimiento sintetico"
# A high sequence value: seguimiento.secuencia is UNIQUE across the whole table.
SECUENCIA = 10**12 + uuid.uuid4().int % 10**9


def _create_topic(name: str) -> None:
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result()


def _delete_topic(name: str) -> None:
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.delete_topics([name]).values():
        future.result()


def _owner(sql: str, params: tuple) -> list[tuple]:
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _event(aggregate_id: uuid.UUID, event_type: str, version: int, payload: dict) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type="tratamiento",
        aggregate_id=aggregate_id,
        event_type=event_type,
        aggregate_version=version,
        created_at=datetime.now(UTC),
        payload=payload,
    )


@requires_all
def test_treatment_events_reach_the_replica_once_and_in_order():
    topic = f"medicalbooking.events.test-{uuid.uuid4().hex[:12]}"
    consumer_name = f"test-{uuid.uuid4().hex[:16]}"
    tratamiento_id = uuid.uuid4()
    treatment = Treatment(
        id=str(tratamiento_id),
        cita_id=str(uuid.uuid4()),
        paciente_id=str(uuid.uuid4()),
        paciente_user_id=str(uuid.uuid4()),
        medico_id=str(uuid.uuid4()),
        medico_user_id=str(uuid.uuid4()),
        descripcion=DESCRIPCION,
        estado="ACTIVO",
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=None,
    )
    finalized = replace(treatment, estado="FINALIZADO", fecha_fin=date(2026, 11, 10))
    followup = FollowUp(
        id=str(uuid.uuid4()),
        tratamiento_id=str(tratamiento_id),
        nota=NOTA,
        registrado_en=datetime(2026, 11, 10, 12, 0),
    )
    events = [
        _event(
            tratamiento_id,
            "tratamiento.creado",
            1,
            {"tratamiento": treatment_snapshot(treatment)},
        ),
        _event(
            tratamiento_id,
            "tratamiento.actualizado",
            2,
            {"tratamiento": treatment_snapshot(finalized)},
        ),
        _event(
            tratamiento_id,
            "seguimiento.registrado",
            3,
            {"seguimiento": followup_snapshot(followup, SECUENCIA)},
        ),
    ]
    _create_topic(topic)
    try:
        handler = KafkaOutboxHandler(build_kafka_producer(BOOTSTRAP), topic=topic)
        for event in events:
            handler.handle(event)
            handler.handle(event)  # the same event delivered twice (a retry after a lost ack)

        sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
        applied = consume(
            BOOTSTRAP,
            topic,
            sink,
            group_id=f"test-{uuid.uuid4().hex}",
            consumer_name=consumer_name,
            max_idle_polls=5,
        )

        assert applied == len(events)
        assert _owner(
            "SELECT estado, fecha_fin, version, descripcion FROM replica.tratamientos "
            "WHERE id = %s",
            (str(tratamiento_id),),
        ) == [("FINALIZADO", date(2026, 11, 10), 2, DESCRIPCION)]
        assert _owner(
            "SELECT secuencia, nota FROM replica.tratamientos_seguimientos "
            "WHERE tratamiento_id = %s",
            (str(tratamiento_id),),
        ) == [(SECUENCIA, NOTA)]
        processed = _owner(
            "SELECT COUNT(*) FROM replica.eventos_procesados WHERE consumidor = %s",
            (consumer_name,),
        )
        assert processed == [(len(events),)]
    finally:
        _delete_topic(topic)
        with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM replica.tratamientos_seguimientos WHERE tratamiento_id = %s",
                (str(tratamiento_id),),
            )
            cur.execute("DELETE FROM replica.tratamientos WHERE id = %s", (str(tratamiento_id),))
            cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (consumer_name,))
            cur.execute(
                "DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (consumer_name,)
            )

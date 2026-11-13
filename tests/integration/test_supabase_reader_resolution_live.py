"""The replica reader resolves the patient of its own account, live against Supabase.

A synthetic account is projected with the writer, resolved with the reader, and removed in a
finally block. A second account must not resolve to the first account's patient.
Runs only when SUPABASE_WRITER_DATABASE_URL, SUPABASE_READER_DATABASE_URL and
SUPABASE_DATABASE_URL are all set.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AccountNotActive,
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
CONSUMER = f"test-{uuid.uuid4().hex[:16]}"


def _project_account(sink, user: uuid.UUID, patient: uuid.UUID) -> None:
    envelope = {
        "event_id": str(uuid.uuid4()),
        "aggregate_type": "usuario",
        "aggregate_id": str(user),
        "event_type": "usuario.creado",
        "aggregate_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "payload": {
            "usuario": {"id": str(user), "estado": "ACTIVO"},
            # Role 1 is the PACIENTE role of the synced catalog (migration 0003).
            "roles": [1],
            "paciente": {
                "id": str(patient),
                "usuario_id": str(user),
                "nombre": "Ana",
                "apellido": "Sintetica",
            },
        },
    }
    apply_event(envelope, sink, CONSUMER)


def _cleanup(users: list[uuid.UUID], patients: list[uuid.UUID]) -> None:
    with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
        for patient in patients:
            cur.execute("DELETE FROM replica.pacientes WHERE id = %s", (str(patient),))
        for user in users:
            cur.execute("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (str(user),))
            cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (str(user),))
        cur.execute("DELETE FROM replica.watermarks WHERE consumidor = %s", (CONSUMER,))
        cur.execute("DELETE FROM replica.eventos_procesados WHERE consumidor = %s", (CONSUMER,))


@requires_all
def test_an_account_resolves_to_its_own_patient_and_no_other():
    owner_user, other_user = uuid.uuid4(), uuid.uuid4()
    patient = uuid.uuid4()
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    reader = SupabaseReplicaReader(lambda: psycopg.connect(READER, connect_timeout=15))
    try:
        _project_account(sink, owner_user, patient)
        _project_account(sink, other_user, uuid.uuid4())

        assert reader.patient_for(owner_user) == patient
        other = reader.patient_for(other_user)
        assert other != patient
    finally:
        _cleanup([owner_user, other_user], [patient])


@requires_all
def test_an_unknown_account_is_refused_before_anything_is_read():
    reader = SupabaseReplicaReader(lambda: psycopg.connect(READER, connect_timeout=15))

    with pytest.raises(AccountNotActive):
        reader.patient_for(uuid.uuid4())


@requires_all
def test_a_blocked_account_is_refused():
    user, patient = uuid.uuid4(), uuid.uuid4()
    sink = SupabaseProjectionSink(lambda: psycopg.connect(WRITER, connect_timeout=15))
    reader = SupabaseReplicaReader(lambda: psycopg.connect(READER, connect_timeout=15))
    try:
        _project_account(sink, user, patient)
        with psycopg.connect(OWNER, connect_timeout=15) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE replica.usuarios SET estado = 'BLOQUEADO' WHERE id = %s",
                (str(user),),
            )

        with pytest.raises((AccountNotActive, AccessDenied)):
            reader.patient_for(user)
    finally:
        _cleanup([user], [patient])

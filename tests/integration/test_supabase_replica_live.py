"""Live checks against the Supabase replica as replica_reader. Read-only by design.

Runs only when SUPABASE_READER_DATABASE_URL is set; otherwise every test is skipped.
Nothing is written: the reader role cannot insert, and the one write attempt is rolled back.
"""

import os
import uuid

import psycopg
import pytest
from psycopg import errors

from medicalbooking.modules.replica.infrastructure.supabase_reader import SupabaseReplicaReader

pytestmark = pytest.mark.integration

READER_URL = os.environ.get("SUPABASE_READER_DATABASE_URL")
requires_reader = pytest.mark.skipif(
    not READER_URL, reason="SUPABASE_READER_DATABASE_URL is not set"
)


def _connect():
    return psycopg.connect(READER_URL, connect_timeout=15)


@requires_reader
def test_the_reader_connects_and_sees_no_rows_for_an_unknown_patient():
    reader = SupabaseReplicaReader(_connect)

    result = reader.results_of(uuid.uuid4())

    assert result.orders == ()


@requires_reader
def test_the_reader_cannot_read_the_idempotency_or_lag_tables():
    with _connect() as conn:
        for table in ("replica.eventos_procesados", "replica.watermarks"):
            with conn.transaction(), conn.cursor() as cur:
                with pytest.raises(errors.InsufficientPrivilege):
                    cur.execute(f"SELECT count(*) FROM {table}")


@requires_reader
def test_the_reader_cannot_insert_a_patient():
    with _connect() as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO replica.pacientes (id, nombre, apellido, actualizado_en) "
                    "VALUES (%s, 'x', 'y', now())",
                    (str(uuid.uuid4()),),
                )


@requires_reader
def test_without_a_patient_fixed_the_policies_return_nothing():
    with _connect() as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM replica.ordenes_laboratorio")
            assert cur.fetchone()[0] == 0

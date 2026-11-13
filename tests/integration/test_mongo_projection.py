"""MONGODB outbox destination: the same projection as the Supabase replica, on MongoDB (S1-S4).

Needs a MongoDB server in MONGO_TEST_URI (for example mongodb://127.0.0.1:27018). Each test
uses its own database and drops it afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pymongo import MongoClient

from medicalbooking.modules.replica.infrastructure.mongo_projection import MongoOutboxHandler
from medicalbooking.modules.synchronization.application.ports import (
    PermanentDeliveryError,
    TransientDeliveryError,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

pytestmark = pytest.mark.integration

MONGO_URI = os.environ.get("MONGO_TEST_URI")
CREATED = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[MongoClient]:
    if not MONGO_URI:
        pytest.skip("MONGO_TEST_URI is not set")
    connection = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    yield connection
    connection.close()


@pytest.fixture
def database(client: MongoClient):
    name = f"mb_projection_test_{uuid.uuid4().hex[:12]}"
    yield client[name]
    client.drop_database(name)


def _event(
    event_type: str,
    aggregate_id: str,
    version: int,
    payload: dict,
    aggregate_type: str = "usuario",
) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=aggregate_type,
        aggregate_id=uuid.UUID(aggregate_id),
        event_type=event_type,
        aggregate_version=version,
        created_at=CREATED,
        payload=payload,
    )


def _usuario_creado(user_id: str, estado: str = "ACTIVO") -> OutboxEvent:
    return _event(
        "usuario.creado",
        user_id,
        1,
        {
            "usuario": {"id": user_id, "estado": estado},
            "roles": [1],
            "paciente": {
                "id": str(uuid.uuid4()),
                "usuario_id": user_id,
                "nombre": "Ana",
                "apellido": "Demo",
            },
        },
    )


def _usuario_estado(user_id: str, estado: str, version: int) -> OutboxEvent:
    return _event(
        "usuario.activado", user_id, version, {"usuario": {"id": user_id, "estado": estado}}
    )


def test_usuario_creado_projects_the_account_roles_and_patient(database) -> None:
    user_id = str(uuid.uuid4())

    MongoOutboxHandler(database).handle(_usuario_creado(user_id))

    account = database["usuarios"].find_one({"_id": user_id})
    assert account["estado"] == "ACTIVO"
    assert account["roles"] == [1]
    assert account["_v"] == 1
    patient = database["pacientes"].find_one({"usuario_id": user_id})
    assert (patient["nombre"], patient["apellido"]) == ("Ana", "Demo")


def test_replaying_an_event_changes_nothing(database) -> None:
    user_id = str(uuid.uuid4())
    handler = MongoOutboxHandler(database)
    event = _usuario_creado(user_id)

    handler.handle(event)
    handler.handle(event)

    assert database["usuarios"].count_documents({"_id": user_id}) == 1
    assert database["eventos_procesados"].count_documents({}) == 1


def test_an_older_version_never_overwrites_a_newer_state(database) -> None:
    user_id = str(uuid.uuid4())
    handler = MongoOutboxHandler(database)
    handler.handle(_usuario_estado(user_id, "BLOQUEADO", version=3))

    handler.handle(_usuario_estado(user_id, "ACTIVO", version=2))

    account = database["usuarios"].find_one({"_id": user_id})
    assert account["estado"] == "BLOQUEADO"
    assert account["_v"] == 3


def test_a_newer_version_overwrites_the_state(database) -> None:
    user_id = str(uuid.uuid4())
    handler = MongoOutboxHandler(database)
    handler.handle(_usuario_estado(user_id, "ACTIVO", version=1))

    handler.handle(_usuario_estado(user_id, "BLOQUEADO", version=2))

    assert database["usuarios"].find_one({"_id": user_id})["estado"] == "BLOQUEADO"


def test_an_invalid_payload_is_permanent_and_leaves_no_processed_mark(database) -> None:
    event = _event("usuario.creado", str(uuid.uuid4()), 1, {"sin": "usuario"})

    with pytest.raises(PermanentDeliveryError):
        MongoOutboxHandler(database).handle(event)

    assert database["eventos_procesados"].count_documents({"_id": {"$regex": str(event.id)}}) == 0


def test_an_unreachable_server_is_transient(client: MongoClient) -> None:
    unreachable = MongoClient("mongodb://127.0.0.1:27999", serverSelectionTimeoutMS=300)
    try:
        handler = MongoOutboxHandler(unreachable["mb_unreachable"])
        with pytest.raises(TransientDeliveryError):
            handler.handle(_usuario_creado(str(uuid.uuid4())))
    finally:
        unreachable.close()


def test_event_types_without_a_projection_are_skipped(database) -> None:
    event = _event("evento.sin_proyeccion", str(uuid.uuid4()), 1, {}, aggregate_type="otro")

    MongoOutboxHandler(database).handle(event)

    assert database["eventos_procesados"].count_documents({}) == 1
    assert database.list_collection_names() == ["eventos_procesados"]

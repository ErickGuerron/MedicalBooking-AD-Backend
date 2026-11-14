"""MongoDB projection of tratamientos and seguimientos (treatments S11), without a server.

The fake database implements only the operators the sink uses: equality on _id, $lte and $gte on
_v, $set, $max, and the duplicate-key error a real upsert raises when the stored document is newer.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from medicalbooking.modules.replica.infrastructure.mongo_projection import MongoOutboxHandler
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

TRATAMIENTO = "11111111-1111-4111-8111-111111111111"
SEGUIMIENTO = "22222222-2222-4222-8222-222222222222"
CITA = "33333333-3333-4333-8333-333333333333"
PACIENTE = "44444444-4444-4444-8444-444444444444"
MEDICO = "55555555-5555-4555-8555-555555555555"
USUARIO = "66666666-6666-4666-8666-666666666666"
DESCRIPCION = "Fisioterapia lumbar, control semanal con ejercicios de movilidad (sintetico)"
NOTA = "Paciente refiere mejoria del dolor; continuar ejercicios de movilidad (sintetico)."
CREATED = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _matches(doc: dict[str, Any], condition: dict[str, Any]) -> bool:
    for name, expected in condition.items():
        value = doc.get(name)
        if isinstance(expected, dict):
            if "$lte" in expected and not (value is not None and value <= expected["$lte"]):
                return False
            if "$gte" in expected and not (value is not None and value >= expected["$gte"]):
                return False
        elif value != expected:
            return False
    return True


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[Any, dict[str, Any]] = {}

    def find_one(self, condition, projection=None):
        doc = self.docs.get(condition.get("_id"))
        if doc is None or not _matches(doc, condition):
            return None
        return copy.deepcopy(doc)

    def insert_one(self, document) -> None:
        if document["_id"] in self.docs:
            raise DuplicateKeyError("E11000 duplicate key")
        self.docs[document["_id"]] = copy.deepcopy(document)

    def update_one(self, condition, update, upsert=False) -> None:
        key = condition["_id"]
        doc = self.docs.get(key)
        if doc is not None and not _matches(doc, condition):
            if upsert:
                # A real upsert inserts with the same _id and fails: the stored document wins.
                raise DuplicateKeyError("E11000 duplicate key")
            return
        if doc is None:
            if not upsert:
                return
            doc = {"_id": key}
            self.docs[key] = doc
        for name, value in update.get("$set", {}).items():
            doc[name] = value
        for name, value in update.get("$max", {}).items():
            doc[name] = max(doc.get(name, value), value)

    def delete_one(self, condition) -> None:
        self.docs.pop(condition["_id"], None)

    def delete_many(self, condition) -> None:
        for key in [k for k, d in self.docs.items() if _matches(d, condition)]:
            del self.docs[key]


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())


def _event(
    event_type: str,
    aggregate_id: str,
    version: int,
    payload: dict[str, Any],
    aggregate_type: str = "tratamiento",
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


def _tratamiento_payload(descripcion: str, estado: str = "ACTIVO") -> dict[str, Any]:
    return {
        "tratamiento": {
            "id": TRATAMIENTO,
            "cita_id": CITA,
            "paciente_id": PACIENTE,
            "medico_id": MEDICO,
            "descripcion": descripcion,
            "estado": estado,
            "fecha_inicio": "2026-11-01",
            "fecha_fin": None,
        }
    }


def test_tratamiento_creado_upserts_the_treatment_document_with_its_version() -> None:
    database = FakeDatabase()

    MongoOutboxHandler(database).handle(
        _event("tratamiento.creado", TRATAMIENTO, 1, _tratamiento_payload(DESCRIPCION))
    )

    document = database["tratamientos"].docs[TRATAMIENTO]
    assert document["descripcion"] == DESCRIPCION
    assert document["estado"] == "ACTIVO"
    assert document["paciente_id"] == PACIENTE
    assert document["_v"] == 1
    assert "id" not in document


def test_seguimiento_registrado_upserts_the_full_note_in_its_own_collection() -> None:
    database = FakeDatabase()
    payload = {
        "seguimiento": {
            "id": SEGUIMIENTO,
            "tratamiento_id": TRATAMIENTO,
            "secuencia": 7,
            "nota": NOTA,
            "creado_en": CREATED.isoformat(),
        }
    }

    MongoOutboxHandler(database).handle(_event("seguimiento.registrado", TRATAMIENTO, 3, payload))

    document = database["tratamientos_seguimientos"].docs[SEGUIMIENTO]
    assert document["nota"] == NOTA
    assert document["tratamiento_id"] == TRATAMIENTO
    assert document["secuencia"] == 7
    assert document["_v"] == 3


def test_a_stale_treatment_event_does_not_overwrite_a_newer_document() -> None:
    database = FakeDatabase()
    handler = MongoOutboxHandler(database)
    newer = "Control a las dos semanas: ajustar carga (sintetico)."

    handler.handle(_event("tratamiento.actualizado", TRATAMIENTO, 3, _tratamiento_payload(newer)))
    handler.handle(_event("tratamiento.creado", TRATAMIENTO, 1, _tratamiento_payload(DESCRIPCION)))

    document = database["tratamientos"].docs[TRATAMIENTO]
    assert document["descripcion"] == newer
    assert document["_v"] == 3


def test_existing_account_projection_still_writes_the_usuarios_collection() -> None:
    database = FakeDatabase()
    event = _event(
        "usuario.activado",
        USUARIO,
        2,
        {"usuario": {"id": USUARIO, "estado": "ACTIVO"}},
        aggregate_type="usuario",
    )

    MongoOutboxHandler(database).handle(event)

    assert database["usuarios"].docs[USUARIO]["estado"] == "ACTIVO"
    assert database["usuarios"].docs[USUARIO]["_v"] == 2

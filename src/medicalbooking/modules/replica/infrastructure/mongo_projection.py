"""Outbox destination MONGODB: the Supabase replica's projection, applied to MongoDB.

The handler reuses ``apply_event`` and its event mapping, so Mongo and Supabase receive the same
information. Only the sink differs. Each document is keyed by its aggregate id and stores the
aggregate version (``_v``); an update applies only when the stored version is not newer, so an
older event never overwrites a newer state. The processed mark is written after the projection
succeeds, so a failed projection is retried. Writes are idempotent, so a replay changes nothing.

Errors: a MongoDB failure is transient (the dispatcher retries); an event whose payload does not
fit the mapping is permanent. Payloads are never logged.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from medicalbooking.modules.replica.application.projection_consumer import apply_event
from medicalbooking.modules.synchronization.application.ports import (
    PermanentDeliveryError,
    TransientDeliveryError,
)
from medicalbooking.modules.synchronization.domain.events import OutboxEvent

CONSUMER = "replica-mongo"
PROCESSED = "eventos_procesados"


class MongoProjectionSink:
    """Sink used by ``apply_event``: one transaction per event, versioned writes."""

    def __init__(self, database: Database, version: int) -> None:
        self._database = database
        self._version = version

    @contextmanager
    def transaction(self) -> Iterator[_MongoTransaction]:
        tx = _MongoTransaction(self._database, self._version)
        yield tx
        # Reached only when the projection succeeded: a failed projection writes no mark.
        tx.commit()


class _MongoTransaction:
    def __init__(self, database: Database, version: int) -> None:
        self._db = database
        self._version = version
        self._mark: dict[str, Any] | None = None

    def mark_processed(self, event_id: str, consumer: str, tipo: str, created_at: str) -> bool:
        key = f"{consumer}:{event_id}"
        if self._db[PROCESSED].find_one({"_id": key}, {"_id": 1}) is not None:
            return False
        self._mark = {"_id": key, "tipo": tipo, "recibido_en": created_at}
        return True

    def commit(self) -> None:
        if self._mark is not None:
            try:
                self._db[PROCESSED].insert_one(self._mark)
            except DuplicateKeyError:
                pass  # a concurrent delivery of the same event already marked it

    # --- versioned writes -------------------------------------------------------------

    def _set(self, collection: str, key: Any, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into one document, but only when its stored version is not newer.

        A duplicate-key error means the stored document is newer: the update is ignored.
        """
        clean = {name: _mongo_value(value) for name, value in fields.items() if value is not None}
        clean["_v"] = self._version
        try:
            self._db[collection].update_one(
                {"_id": key, "_v": {"$lte": self._version}},
                {"$set": clean},
                upsert=True,
            )
        except DuplicateKeyError:
            return

    def _delete(self, collection: str, key: Any) -> None:
        self._db[collection].delete_one({"_id": key})

    def upsert_account(self, usuario_id: str, estado: str) -> None:
        self._set("usuarios", usuario_id, {"estado": estado})

    def replace_roles(self, usuario_id: str, roles: list[int]) -> None:
        self._set("usuarios", usuario_id, {"roles": list(roles)})

    def upsert_patient(self, id: str, usuario_id: str, nombre: str, apellido: str) -> None:
        self._set(
            "pacientes",
            id,
            {"usuario_id": usuario_id, "nombre": nombre, "apellido": apellido},
        )

    def upsert_order(self, orden: dict[str, Any]) -> None:
        fields = {name: value for name, value in orden.items() if name not in ("id", "detalles")}
        self._set("ordenes_laboratorio", orden["id"], fields)

    def upsert_detail(
        self, detalle_id: str, orden_id: str, estado: str, tipo_prueba_id: Any
    ) -> None:
        self._set(
            "detalles_laboratorio",
            detalle_id,
            {"orden_id": orden_id, "estado": estado, "tipo_prueba_id": tipo_prueba_id},
        )

    def set_order_state(self, orden_id: str, estado: str) -> None:
        self._set("ordenes_laboratorio", orden_id, {"estado": estado})

    def upsert_results(self, detalle_id: str, resultados: list[dict[str, Any]]) -> None:
        self._set("resultados_laboratorio", detalle_id, {"resultados": resultados})

    def upsert_proposal(self, propuesta: dict[str, Any], hallazgos: list[dict[str, Any]]) -> None:
        fields = {name: value for name, value in propuesta.items() if name != "id"}
        fields["hallazgos"] = hallazgos
        self._set("propuestas_clinicas", propuesta["id"], fields)

    def delete_proposal(self, propuesta_id: str) -> None:
        self._delete("propuestas_clinicas", propuesta_id)

    def upsert_invoice(self, factura: dict[str, Any]) -> None:
        fields = {name: value for name, value in factura.items() if name not in ("id", "detalles")}
        self._set("facturas", factura["id"], fields)

    def upsert_invoice_detail(self, detalle: dict[str, Any], factura_id: str) -> None:
        fields = {name: value for name, value in detalle.items() if name != "id"}
        fields["factura_id"] = factura_id
        self._set("factura_detalles", detalle["id"], fields)

    def set_invoice_state(self, factura_id: str, estado: str) -> None:
        self._set("facturas", factura_id, {"estado": estado})

    def upsert_payment(self, pago: dict[str, Any]) -> None:
        fields = {name: value for name, value in pago.items() if name != "id"}
        self._set("pagos", pago["id"], fields)

    def upsert_refund(self, reembolso: dict[str, Any]) -> None:
        fields = {name: value for name, value in reembolso.items() if name != "id"}
        self._set("reembolsos", reembolso["id"], fields)

    def upsert_cita(self, cita: dict[str, Any]) -> None:
        fields = {name: value for name, value in cita.items() if name != "id"}
        self._set("citas", cita["id"], fields)


def _mongo_value(value: Any) -> Any:
    """Values BSON cannot encode: exact decimals as strings, dates as datetimes."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time())
    if isinstance(value, list):
        return [_mongo_value(item) for item in value]
    if isinstance(value, dict):
        return {name: _mongo_value(item) for name, item in value.items()}
    return value


class MongoOutboxHandler:
    destination_code = "MONGODB"

    def __init__(self, database: Database) -> None:
        self._database = database

    def handle(self, event: OutboxEvent) -> None:
        envelope = {
            "event_id": str(event.id),
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id),
            "event_type": event.event_type,
            "aggregate_version": event.aggregate_version,
            "created_at": event.created_at.isoformat(),
            "payload": event.payload,
        }
        sink = MongoProjectionSink(self._database, event.aggregate_version)
        try:
            apply_event(envelope, sink, CONSUMER)
        except PyMongoError as error:
            # Error type only: driver messages may carry connection data.
            raise TransientDeliveryError("mongodb did not apply the event") from error
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise PermanentDeliveryError(
                "event payload does not fit the MongoDB projection"
            ) from error

"""Apply one outbox event to the Supabase replica (kafka-replication T2/T3).

Each event is applied inside one transaction together with its processed mark, so a
failed write leaves neither a projection nor a mark. A duplicate event_id is skipped. The
projections are upserts or deletes keyed by id, so replaying an event is harmless.

Events of other types are ignored: the replica projects only what it needs (spec 8.3).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

_REVIEWED = frozenset({"ACEPTADA", "CORREGIDA"})


class ProjectionSink(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...


def apply_event(envelope: dict[str, Any], sink: Any, consumer: str) -> bool:
    """Return True when the event was applied, False when it was already processed."""
    with sink.transaction() as tx:
        if not tx.mark_processed(envelope["event_id"], consumer, envelope["event_type"]):
            return False
        handler = _HANDLERS.get(envelope["event_type"])
        if handler is not None:
            handler(envelope, tx)
        return True


def _usuario_creado(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    usuario = payload["usuario"]
    tx.upsert_account(usuario["id"], usuario["estado"])
    tx.replace_roles(usuario["id"], payload.get("roles", []))
    paciente = payload.get("paciente")
    if paciente is not None:
        tx.upsert_patient(**paciente)


def _usuario_activado(envelope: dict[str, Any], tx: Any) -> None:
    usuario = envelope["payload"]["usuario"]
    tx.upsert_account(usuario["id"], usuario["estado"])


def _orden_creada(envelope: dict[str, Any], tx: Any) -> None:
    orden = envelope["payload"]["orden"]
    tx.upsert_order(orden)
    for detalle in orden.get("detalles", []):
        tx.upsert_detail(detalle["id"], orden["id"], detalle["estado"])


def _resultado_registrado(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    detalle = payload["detalle"]
    tx.upsert_detail(detalle["id"], envelope["aggregate_id"], detalle["estado"])
    tx.set_order_state(envelope["aggregate_id"], payload["orden_estado"])
    tx.upsert_results(detalle["id"], payload.get("resultados", []))


def _propuesta_revisada(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    propuesta = payload["propuesta"]
    if propuesta["estado"] in _REVIEWED:
        tx.upsert_proposal(propuesta, payload.get("hallazgos", []))
    else:
        tx.delete_proposal(propuesta["id"])


_HANDLERS: dict[str, Callable[[dict[str, Any], Any], None]] = {
    "usuario.creado": _usuario_creado,
    "usuario.activado": _usuario_activado,
    "orden_laboratorio.creada": _orden_creada,
    "resultado_laboratorio.registrado": _resultado_registrado,
    "propuesta.revisada": _propuesta_revisada,
}

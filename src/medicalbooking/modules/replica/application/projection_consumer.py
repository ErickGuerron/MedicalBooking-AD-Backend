"""Apply one outbox event to the Supabase replica (kafka-replication T2/T3).

Each event is applied inside one transaction together with its processed mark, so a
failed write leaves neither a projection nor a mark. A duplicate event_id is skipped. The
projections are upserts or deletes keyed by id, so replaying an event is harmless.

Events of other types are ignored: the replica projects only what it needs (spec 8.3).

Delete events (emitted in the same SQL Server transaction as the delete):
- ``usuario.eliminado``: aggregate ``usuario``, payload ``{"usuario": {"id"}}``. Removes the
  account, its roles and its patient profile (keyed by usuario_id).
- ``cita.eliminada``: aggregate ``cita``, payload ``{"cita": {"id"}}``. Removes the appointment.
Deleting a row that is already absent is a no-op, never an error.
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
        if not tx.mark_processed(
            envelope["event_id"],
            consumer,
            envelope["event_type"],
            envelope["created_at"],
        ):
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
        tx.upsert_detail(detalle["id"], orden["id"], detalle["estado"], detalle["tipo_prueba_id"])


def _resultado_registrado(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    detalle = payload["detalle"]
    # The detail keeps its test type from the creation event; only its state changes here.
    tx.upsert_detail(detalle["id"], envelope["aggregate_id"], detalle["estado"], None)
    tx.set_order_state(envelope["aggregate_id"], payload["orden_estado"])
    tx.upsert_results(detalle["id"], payload.get("resultados", []))


def _propuesta_revisada(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    propuesta = payload["propuesta"]
    if propuesta["estado"] in _REVIEWED:
        tx.upsert_proposal(propuesta, payload.get("hallazgos", []))
    else:
        tx.delete_proposal(propuesta["id"])


_FACTURA_FIELDS = (
    "id",
    "paciente_id",
    "cita_id",
    "numero_factura",
    "subtotal",
    "impuestos",
    "total",
    "moneda",
    "estado",
    "creada_en",
)
_DETALLE_FIELDS = ("id", "servicio_id", "descripcion", "cantidad", "precio_unitario", "subtotal")
_PAGO_FIELDS = ("id", "factura_id", "monto", "moneda", "estado", "creado_en", "pagado_en")
_REEMBOLSO_FIELDS = (
    "id",
    "pago_id",
    "monto",
    "estado",
    "solicitado_en",
    "procesado_en",
    "motivo_codigo",
)


def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Only the replica's own columns: gateway identifiers and keys never reach the sink."""
    return {name: source.get(name) for name in fields}


def _factura_creada(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    factura = payload["factura"]
    tx.upsert_invoice(_pick(factura, _FACTURA_FIELDS))
    for detalle in payload.get("detalles", []):
        tx.upsert_invoice_detail(_pick(detalle, _DETALLE_FIELDS), factura["id"])


def _project_payment(payload: dict[str, Any], tx: Any) -> None:
    factura = payload["factura"]
    tx.set_invoice_state(factura["id"], factura["estado"])
    tx.upsert_payment(_pick(payload["pago"], _PAGO_FIELDS))


def _pago_actualizado(envelope: dict[str, Any], tx: Any) -> None:
    _project_payment(envelope["payload"], tx)


def _reembolso_actualizado(envelope: dict[str, Any], tx: Any) -> None:
    payload = envelope["payload"]
    _project_payment(payload, tx)
    tx.upsert_refund(_pick(payload["reembolso"], _REEMBOLSO_FIELDS))


_CITA_FIELDS = (
    "id",
    "paciente_id",
    "servicio_id",
    "estado",
    "reservada_en",
    "confirmada_en",
    "cancelada_en",
    "motivo_cancelacion_codigo",
    "motivo_cancelacion_texto",
    "inicio",
    "fin",
)


def _cita_actualizada(envelope: dict[str, Any], tx: Any) -> None:
    """A snapshot is the full state of the appointment: the replica keeps only its own columns."""
    tx.upsert_cita(_pick(envelope["payload"]["cita"], _CITA_FIELDS))


def _usuario_eliminado(envelope: dict[str, Any], tx: Any) -> None:
    tx.delete_account(envelope["payload"]["usuario"]["id"])


def _cita_eliminada(envelope: dict[str, Any], tx: Any) -> None:
    tx.delete_cita(envelope["payload"]["cita"]["id"])


_TRATAMIENTO_FIELDS = (
    "id",
    "cita_id",
    "paciente_id",
    "medico_id",
    "descripcion",
    "estado",
    "fecha_inicio",
    "fecha_fin",
)
_SEGUIMIENTO_FIELDS = ("id", "tratamiento_id", "secuencia", "nota", "creado_en")


def _tratamiento_proyectado(envelope: dict[str, Any], tx: Any) -> None:
    """Created and updated events both carry the full treatment, with its text.

    The envelope version orders the writes: the sink keeps a stored row only over an older one.
    """
    tratamiento = _pick(envelope["payload"]["tratamiento"], _TRATAMIENTO_FIELDS)
    tx.upsert_tratamiento(tratamiento, envelope["aggregate_version"])


def _seguimiento_registrado(envelope: dict[str, Any], tx: Any) -> None:
    tx.upsert_seguimiento(_pick(envelope["payload"]["seguimiento"], _SEGUIMIENTO_FIELDS))


_HANDLERS: dict[str, Callable[[dict[str, Any], Any], None]] = {
    "usuario.creado": _usuario_creado,
    "usuario.activado": _usuario_activado,
    "usuario.eliminado": _usuario_eliminado,
    "orden_laboratorio.creada": _orden_creada,
    "resultado_laboratorio.registrado": _resultado_registrado,
    "propuesta.revisada": _propuesta_revisada,
    "factura.creada": _factura_creada,
    "pago.actualizado": _pago_actualizado,
    "reembolso.actualizado": _reembolso_actualizado,
    "cita.actualizada": _cita_actualizada,
    "cita.eliminada": _cita_eliminada,
    "tratamiento.creado": _tratamiento_proyectado,
    "tratamiento.actualizado": _tratamiento_proyectado,
    "seguimiento.registrado": _seguimiento_registrado,
}

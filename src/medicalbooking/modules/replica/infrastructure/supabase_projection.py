"""Supabase projection sink: the writes behind the replica consumer (kafka-replication T2/T3).

Runs as replica_writer. Every write is an upsert or a delete keyed by id, so replaying an
event leaves the same rows. A detail update for a detail the replica does not hold yet
raises, so the event is retried after the event that creates it. The processed mark and
the watermark are written in the same transaction as the projection.

No tombstone table: the replication topic is keyed by aggregate id (kafka-replication S1), so
Kafka delivers an aggregate's events in version order and an older upsert never follows its
delete. The replica's deletes are plain DELETE statements, and a missing row is a no-op.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

_MARK = """
INSERT INTO replica.eventos_procesados (evento_id, consumidor, tipo_agregado)
VALUES (%s, %s, %s)
ON CONFLICT (evento_id, consumidor) DO NOTHING
"""

_WATERMARK = """
INSERT INTO replica.watermarks (consumidor, ultimo_evento_en, ultimo_sincronizado_en)
VALUES (%s, %s, now())
ON CONFLICT (consumidor) DO UPDATE SET
  ultimo_evento_en = GREATEST(replica.watermarks.ultimo_evento_en, excluded.ultimo_evento_en),
  ultimo_sincronizado_en = now()
"""


class _Transaction:
    def __init__(self, cursor: Any) -> None:
        self._cur = cursor

    def mark_processed(self, event_id: str, consumer: str, tipo: str, created_at: str) -> bool:
        # No RETURNING: the writer has INSERT on this table and no SELECT, by design.
        self._cur.execute(_MARK, (event_id, consumer, tipo))
        if self._cur.rowcount == 0:
            return False
        self._cur.execute(_WATERMARK, (consumer, created_at))
        return True

    def upsert_account(self, usuario_id: str, estado: str) -> None:
        self._cur.execute(
            "INSERT INTO replica.usuarios (id, estado, actualizado_en) VALUES (%s, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado, actualizado_en = now()",
            (usuario_id, estado),
        )

    def replace_roles(self, usuario_id: str, roles: list[int]) -> None:
        self._cur.execute("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (usuario_id,))
        for rol_id in roles:
            self._cur.execute(
                "INSERT INTO replica.usuarios_roles (usuario_id, rol_id) VALUES (%s, %s)",
                (usuario_id, rol_id),
            )

    def upsert_patient(self, id: str, usuario_id: str, nombre: str, apellido: str) -> None:
        self._cur.execute(
            "INSERT INTO replica.pacientes (id, usuario_id, nombre, apellido, actualizado_en) "
            "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (id) DO UPDATE SET "
            "usuario_id = excluded.usuario_id, nombre = excluded.nombre, "
            "apellido = excluded.apellido, actualizado_en = now()",
            (id, usuario_id, nombre, apellido),
        )

    def upsert_order(self, orden: dict[str, Any]) -> None:
        self._cur.execute(
            "INSERT INTO replica.ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, "
            "prioridad, estado, actualizado_en) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado, actualizado_en = now()",
            (
                orden["id"],
                orden["paciente_id"],
                orden["medico_id"],
                orden["fecha_orden"],
                orden["prioridad"],
                orden["estado"],
            ),
        )

    def upsert_detail(
        self, detalle_id: str, orden_id: str, estado: str, tipo_prueba_id: str | None
    ) -> None:
        if tipo_prueba_id is None:
            self._cur.execute(
                "UPDATE replica.ordenes_laboratorio_detalle "
                "SET estado = %s, actualizado_en = now() "
                "WHERE id = %s",
                (estado, detalle_id),
            )
            if self._cur.rowcount == 0:
                raise LookupError("detail not projected yet; retry after its creation event")
            return
        self._cur.execute(
            "INSERT INTO replica.ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, "
            "estado, actualizado_en) VALUES (%s, %s, %s, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado, actualizado_en = now()",
            (detalle_id, orden_id, tipo_prueba_id, estado),
        )

    def set_order_state(self, orden_id: str, estado: str) -> None:
        self._cur.execute(
            "UPDATE replica.ordenes_laboratorio SET estado = %s, actualizado_en = now() "
            "WHERE id = %s",
            (estado, orden_id),
        )
        if self._cur.rowcount == 0:
            raise LookupError("order not projected yet; retry after its creation event")

    def upsert_results(self, detalle_id: str, resultados: list[dict[str, Any]]) -> None:
        for item in resultados:
            self._cur.execute(
                "INSERT INTO replica.resultados_laboratorio (id, detalle_orden_id, "
                "nombre_analito, valor_texto, valor_numerico, unidad, referencia_min, "
                "referencia_max, interpretacion, validado_en, actualizado_en) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (id) DO NOTHING",
                (
                    item["id"],
                    detalle_id,
                    item["nombre_analito"],
                    item.get("valor_texto"),
                    item.get("valor_numerico"),
                    item.get("unidad"),
                    item.get("referencia_min"),
                    item.get("referencia_max"),
                    item.get("interpretacion"),
                    item["validado_en"],
                ),
            )

    def upsert_proposal(self, propuesta: dict[str, Any], hallazgos: list[dict[str, Any]]) -> None:
        self._cur.execute(
            "INSERT INTO replica.propuestas_clinicas (id, orden_id, estado, resumen_propuesto, "
            "conclusion_medica, revisado_en, actualizado_en) "
            "VALUES (%s, %s, %s, %s, %s, %s, now()) ON CONFLICT (id) DO UPDATE SET "
            "estado = excluded.estado, resumen_propuesto = excluded.resumen_propuesto, "
            "conclusion_medica = excluded.conclusion_medica, "
            "revisado_en = excluded.revisado_en, actualizado_en = now()",
            (
                propuesta["id"],
                propuesta["orden_id"],
                propuesta["estado"],
                propuesta.get("resumen_propuesto"),
                propuesta.get("conclusion_medica"),
                propuesta.get("revisado_en"),
            ),
        )
        self._cur.execute(
            "DELETE FROM replica.propuestas_hallazgos WHERE propuesta_id = %s",
            (propuesta["id"],),
        )
        for item in hallazgos:
            self._cur.execute(
                "INSERT INTO replica.propuestas_hallazgos (id, propuesta_id, tipo, explicacion, "
                "regla_version, actualizado_en) VALUES (%s, %s, %s, %s, %s, now())",
                (
                    item["id"],
                    propuesta["id"],
                    item["tipo"],
                    item.get("explicacion"),
                    item["regla_version"],
                ),
            )

    def delete_proposal(self, propuesta_id: str) -> None:
        self._cur.execute(
            "DELETE FROM replica.propuestas_hallazgos WHERE propuesta_id = %s", (propuesta_id,)
        )
        self._cur.execute("DELETE FROM replica.propuestas_clinicas WHERE id = %s", (propuesta_id,))

    def delete_account(self, usuario_id: str) -> None:
        self._cur.execute("DELETE FROM replica.usuarios_roles WHERE usuario_id = %s", (usuario_id,))
        self._cur.execute("DELETE FROM replica.pacientes WHERE usuario_id = %s", (usuario_id,))
        self._cur.execute("DELETE FROM replica.usuarios WHERE id = %s", (usuario_id,))

    def delete_cita(self, cita_id: str) -> None:
        self._cur.execute("DELETE FROM replica.citas WHERE id = %s", (cita_id,))

    def upsert_invoice(self, factura: dict[str, Any]) -> None:
        self._cur.execute(
            "INSERT INTO replica.facturas (id, paciente_id, cita_id, numero_factura, subtotal, "
            "impuestos, total, moneda, estado, creada_en) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado",
            (
                factura["id"],
                factura["paciente_id"],
                factura["cita_id"],
                factura["numero_factura"],
                factura["subtotal"],
                factura["impuestos"],
                factura["total"],
                factura["moneda"],
                factura["estado"],
                factura["creada_en"],
            ),
        )

    def upsert_invoice_detail(self, detalle: dict[str, Any], factura_id: str) -> None:
        self._cur.execute(
            "INSERT INTO replica.facturas_detalle (id, factura_id, servicio_id, descripcion, "
            "cantidad, precio_unitario, subtotal) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (
                detalle["id"],
                factura_id,
                detalle["servicio_id"],
                detalle["descripcion"],
                detalle["cantidad"],
                detalle["precio_unitario"],
                detalle["subtotal"],
            ),
        )

    def set_invoice_state(self, factura_id: str, estado: str) -> None:
        self._cur.execute(
            "UPDATE replica.facturas SET estado = %s WHERE id = %s", (estado, factura_id)
        )
        if self._cur.rowcount == 0:
            raise LookupError("invoice not projected yet; retry after its creation event")

    def upsert_payment(self, pago: dict[str, Any]) -> None:
        self._cur.execute(
            "INSERT INTO replica.pagos (id, factura_id, monto, moneda, estado, creado_en, "
            "pagado_en) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
            "estado = excluded.estado, pagado_en = excluded.pagado_en",
            (
                pago["id"],
                pago["factura_id"],
                pago["monto"],
                pago["moneda"],
                pago["estado"],
                pago["creado_en"],
                pago.get("pagado_en"),
            ),
        )

    def upsert_refund(self, reembolso: dict[str, Any]) -> None:
        self._cur.execute(
            "INSERT INTO replica.reembolsos (id, pago_id, monto, estado, solicitado_en, "
            "procesado_en, motivo_codigo) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado, "
            "procesado_en = excluded.procesado_en",
            (
                reembolso["id"],
                reembolso["pago_id"],
                reembolso["monto"],
                reembolso["estado"],
                reembolso["solicitado_en"],
                reembolso.get("procesado_en"),
                reembolso["motivo_codigo"],
            ),
        )

    def upsert_cita(self, cita: dict[str, Any]) -> None:
        self._cur.execute(
            "INSERT INTO replica.citas (id, paciente_id, servicio_id, estado, reservada_en, "
            "confirmada_en, cancelada_en, motivo_cancelacion_codigo, motivo_cancelacion_texto, "
            "inicio, fin) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET estado = excluded.estado, "
            "confirmada_en = excluded.confirmada_en, cancelada_en = excluded.cancelada_en, "
            "motivo_cancelacion_codigo = excluded.motivo_cancelacion_codigo, "
            "motivo_cancelacion_texto = excluded.motivo_cancelacion_texto, "
            "inicio = excluded.inicio, fin = excluded.fin",
            (
                cita["id"],
                cita["paciente_id"],
                cita["servicio_id"],
                cita["estado"],
                cita["reservada_en"],
                cita.get("confirmada_en"),
                cita.get("cancelada_en"),
                cita.get("motivo_cancelacion_codigo"),
                cita.get("motivo_cancelacion_texto"),
                cita["inicio"],
                cita["fin"],
            ),
        )

    def upsert_tratamiento(self, tratamiento: dict[str, Any], version: int) -> None:
        """A newer version replaces the row; an older or equal one leaves it unchanged."""
        self._cur.execute(
            "INSERT INTO replica.tratamientos (id, cita_id, paciente_id, medico_id, descripcion, "
            "estado, fecha_inicio, fecha_fin, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET descripcion = excluded.descripcion, "
            "estado = excluded.estado, fecha_fin = excluded.fecha_fin, "
            "version = excluded.version "
            "WHERE replica.tratamientos.version < excluded.version",
            (
                tratamiento["id"],
                tratamiento["cita_id"],
                tratamiento["paciente_id"],
                tratamiento["medico_id"],
                tratamiento["descripcion"],
                tratamiento["estado"],
                tratamiento["fecha_inicio"],
                tratamiento.get("fecha_fin"),
                version,
            ),
        )

    def upsert_seguimiento(self, seguimiento: dict[str, Any]) -> None:
        """A follow-up is inserted once: notes are append-only and the writer has no SELECT here.

        INSERT ... ON CONFLICT needs SELECT on the conflict target, which replica_writer lacks on
        this table (migration s0010 grants INSERT only). A replay never reaches this statement:
        apply_event marks the event processed in the same transaction and skips a duplicate.
        """
        self._cur.execute(
            "INSERT INTO replica.tratamientos_seguimientos (id, tratamiento_id, secuencia, nota, "
            "creado_en) VALUES (%s, %s, %s, %s, %s)",
            (
                seguimiento["id"],
                seguimiento["tratamiento_id"],
                seguimiento["secuencia"],
                seguimiento["nota"],
                seguimiento["creado_en"],
            ),
        )


class SupabaseProjectionSink:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @contextmanager
    def transaction(self) -> Iterator[_Transaction]:
        connection = self._connect()
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    yield _Transaction(cursor)
        finally:
            connection.close()

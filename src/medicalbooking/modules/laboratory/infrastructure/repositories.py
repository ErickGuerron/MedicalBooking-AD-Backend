"""SQL Server adapter for the laboratory ports (E3).

Every state change locks the order row (UPDLOCK) first, so two concurrent writers cannot
both complete a detail, both review, or both assign the same outbox version. Results are
only inserted, never updated: a correction would be a new row, not a rewrite. Parameterized
SQL only.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.laboratory.application.ports import (
    AuditEvent,
    LabDetail,
    LabOrder,
    LabResult,
    NewDocument,
    NewOrder,
    ResultEntry,
    StoredDocument,
)
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the API uses lower case."""
    return str(uuid.UUID(str(value)))


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _as_guid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


class SqlLabStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---------- lookups ----------

    def analyte_linked_to_test(self, analito_id: str, tipo_prueba_id: str) -> bool:
        analito, prueba = _as_guid(analito_id), _as_guid(tipo_prueba_id)
        if analito is None or prueba is None:
            return False
        with self._engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT 1 FROM conocimiento_pruebas_analitos "
                    "WHERE analito_id = :a AND tipo_prueba_id = :t"
                ),
                {"a": analito, "t": prueba},
            ).first()
        return found is not None

    def analyte_exists(self, analito_id: str) -> bool:
        guid = _as_guid(analito_id)
        if guid is None:
            return False
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM conocimiento_analitos WHERE id = :a AND activo = 1"),
                {"a": guid},
            ).first()
        return found is not None

    def patient_profile(self, paciente_id: str) -> tuple[str | None, date | None]:
        guid = _as_guid(paciente_id)
        if guid is None:
            return None, None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT sexo_registrado, fecha_nacimiento FROM pacientes WHERE id = :p"),
                {"p": guid},
            ).first()
        return (None, None) if row is None else (row.sexo_registrado, row.fecha_nacimiento)

    def validated_references(self, analito_id: str, unidad: str, on: date) -> list[Reference]:
        guid = _as_guid(analito_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, version, unidad, referencia_min, referencia_max, sexo_aplicable, "
                    "edad_min_dias, edad_max_dias, metodo_analitico, tipo_muestra, contexto, "
                    "fecha_inicio, fecha_fin FROM conocimiento_referencias "
                    "WHERE analito_id = :a AND estado = 'VALIDADA' AND unidad = :u "
                    "AND fecha_inicio <= :hoy AND (fecha_fin IS NULL OR fecha_fin >= :hoy)"
                ),
                {"a": guid, "u": unidad, "hoy": on},
            ).all()
        return [
            Reference(
                id=_canonical(row.id),
                version=row.version,
                unidad=row.unidad,
                minimo=row.referencia_min,
                maximo=row.referencia_max,
                sexo=row.sexo_aplicable,
                edad_min_dias=row.edad_min_dias,
                edad_max_dias=row.edad_max_dias,
                metodo=row.metodo_analitico,
                tipo_muestra=row.tipo_muestra,
                contexto=row.contexto,
                fecha_inicio=row.fecha_inicio,
                fecha_fin=row.fecha_fin,
            )
            for row in rows
        ]

    def resolve_patient_id(self, user_id: str) -> str | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                text("SELECT id FROM pacientes WHERE usuario_id = :u"), {"u": user_id}
            ).scalar()
        return None if value is None else _canonical(value)

    def resolve_medico_id(self, user_id: str) -> str | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                text("SELECT id FROM medicos WHERE usuario_id = :u"), {"u": user_id}
            ).scalar()
        return None if value is None else _canonical(value)

    def patient_exists(self, paciente_id: str) -> bool:
        guid = _as_guid(paciente_id)
        if guid is None:
            return False
        with self._engine.connect() as conn:
            return (
                conn.execute(text("SELECT 1 FROM pacientes WHERE id = :p"), {"p": guid}).first()
                is not None
            )

    def active_test_ids(self, ids: list[str]) -> set[str]:
        guids = [g for g in (_as_guid(i) for i in ids) if g is not None]
        if not guids:
            return set()
        placeholders = ", ".join(f":t{i}" for i in range(len(guids)))
        params = {f"t{i}": g for i, g in enumerate(guids)}
        # Only generated markers (:t0, :t1, ...) enter the SQL; values are bound parameters.
        query = (
            "SELECT id FROM tipos_prueba_laboratorio "  # noqa: S608
            f"WHERE activo = 1 AND id IN ({placeholders})"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(query),
                params,
            ).all()
        return {_canonical(row.id) for row in rows}

    # ---------- orders ----------

    def create_order(self, command: NewOrder) -> LabOrder:
        now = _utc_naive(datetime.now(UTC))
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, "
                    "prioridad, estado, observaciones) "
                    "VALUES (:id, :p, :m, :ahora, :prioridad, 'SOLICITADA', :obs)"
                ),
                {
                    "id": command.order_id,
                    "p": command.paciente_id,
                    "m": command.medico_id,
                    "ahora": now,
                    "prioridad": command.prioridad,
                    "obs": command.observaciones,
                },
            )
            for test_id in command.tipo_prueba_ids:
                conn.execute(
                    text(
                        "INSERT INTO ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, "
                        "estado) VALUES (:id, :o, :t, 'PENDIENTE')"
                    ),
                    {"id": str(uuid.uuid4()), "o": command.order_id, "t": test_id},
                )
            _outbox(
                conn, command.order_id, "orden_laboratorio.creada", {"estado": "SOLICITADA"}, now
            )
        order = self.get_order(command.order_id)
        assert order is not None
        return order

    def get_order(self, order_id: str) -> LabOrder | None:
        guid = _as_guid(order_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            header = conn.execute(
                text(
                    "SELECT o.id, o.paciente_id, pa.usuario_id AS paciente_user_id, o.medico_id, "
                    "me.usuario_id AS medico_user_id, o.prioridad, o.estado, o.observaciones "
                    "FROM ordenes_laboratorio o "
                    "JOIN pacientes pa ON pa.id = o.paciente_id "
                    "JOIN medicos me ON me.id = o.medico_id WHERE o.id = :o"
                ),
                {"o": guid},
            ).first()
            if header is None:
                return None
            detail_rows = conn.execute(
                text(
                    "SELECT id, tipo_prueba_id, estado FROM ordenes_laboratorio_detalle "
                    "WHERE orden_id = :o ORDER BY id"
                ),
                {"o": guid},
            ).all()
        return LabOrder(
            id=_canonical(header.id),
            paciente_id=_canonical(header.paciente_id),
            paciente_user_id=_canonical(header.paciente_user_id),
            medico_id=_canonical(header.medico_id),
            medico_user_id=_canonical(header.medico_user_id),
            prioridad=header.prioridad,
            estado=header.estado,
            observaciones=header.observaciones,
            details=tuple(
                LabDetail(
                    id=_canonical(row.id),
                    tipo_prueba_id=_canonical(row.tipo_prueba_id),
                    estado=row.estado,
                )
                for row in detail_rows
            ),
        )

    # ---------- results ----------

    def record_results(
        self,
        order_id: str,
        detail_id: str,
        entries: list[ResultEntry],
        now: datetime,
        actor_user_id: str,
    ) -> str:
        return retry_on_deadlock(
            lambda: self._record_results_once(order_id, detail_id, entries, now, actor_user_id)
        )

    def _record_results_once(
        self,
        order_id: str,
        detail_id: str,
        entries: list[ResultEntry],
        now: datetime,
        actor_user_id: str,
    ) -> str:
        moment = _utc_naive(now)
        with self._engine.begin() as conn:
            _lock_order(conn, order_id)
            detail = conn.execute(
                text(
                    "SELECT estado FROM ordenes_laboratorio_detalle WITH (UPDLOCK, ROWLOCK) "
                    "WHERE id = :d AND orden_id = :o"
                ),
                {"d": detail_id, "o": order_id},
            ).first()
            if detail is None:
                return "DETAIL_NOT_FOUND"
            if detail.estado != "PENDIENTE":
                return "DETAIL_ALREADY_COMPLETED"
            for entry in entries:
                conn.execute(
                    text(
                        "INSERT INTO resultados_laboratorio (id, detalle_orden_id, analito_id, "
                        "nombre_analito, valor_texto, valor_numerico, unidad, metodo_analitico, "
                        "tipo_muestra, interpretacion, referencia_aplicada_id, referencia_version, "
                        "referencia_min, referencia_max, validado_por, validado_en) "
                        "VALUES (:id, :d, :analito, :nombre, :texto, :numero, :unidad, :metodo, "
                        ":muestra, :interpretacion, :referencia, :referencia_version, :minimo, "
                        ":maximo, :validador, :ahora)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "d": detail_id,
                        "analito": None if entry.analito_id is None else _as_guid(entry.analito_id),
                        "nombre": entry.nombre_analito,
                        "texto": entry.valor_texto,
                        "numero": entry.valor_numerico,
                        "unidad": entry.unidad,
                        "metodo": entry.metodo_analitico,
                        "muestra": entry.tipo_muestra,
                        "interpretacion": entry.interpretacion,
                        "referencia": entry.referencia_id,
                        "referencia_version": entry.referencia_version,
                        "minimo": entry.referencia_min,
                        "maximo": entry.referencia_max,
                        "validador": actor_user_id,
                        "ahora": moment,
                    },
                )
            conn.execute(
                text(
                    "UPDATE ordenes_laboratorio_detalle SET estado = 'COMPLETADO', "
                    "fecha_resultado = :ahora WHERE id = :d"
                ),
                {"d": detail_id, "ahora": moment},
            )
            pending = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ordenes_laboratorio_detalle "
                    "WHERE orden_id = :o AND estado <> 'COMPLETADO'"
                ),
                {"o": order_id},
            ).scalar_one()
            new_state = "COMPLETADA" if pending == 0 else "EN_PROCESO"
            conn.execute(
                text("UPDATE ordenes_laboratorio SET estado = :e WHERE id = :o"),
                {"e": new_state, "o": order_id},
            )
            _outbox(
                conn,
                order_id,
                "resultado_laboratorio.registrado",
                {"detalle_id": detail_id, "estado_orden": new_state},
                moment,
            )
        return "CREATED"

    def list_results(self, order_id: str) -> list[LabResult]:
        guid = _as_guid(order_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT r.id, r.detalle_orden_id, r.nombre_analito, r.valor_texto, "
                    "r.valor_numerico, r.unidad FROM resultados_laboratorio r "
                    "JOIN ordenes_laboratorio_detalle d ON d.id = r.detalle_orden_id "
                    "WHERE d.orden_id = :o ORDER BY d.id, r.nombre_analito"
                ),
                {"o": guid},
            ).all()
        return [
            LabResult(
                id=_canonical(row.id),
                detalle_id=_canonical(row.detalle_orden_id),
                nombre_analito=row.nombre_analito,
                valor_texto=row.valor_texto,
                valor_numerico=None if row.valor_numerico is None else float(row.valor_numerico),
                unidad=row.unidad,
            )
            for row in rows
        ]

    # ---------- medical review ----------

    def record_review(
        self,
        order_id: str,
        conclusion: str,
        recomendaciones: str | None,
        now: datetime,
        actor_user_id: str,
    ) -> str:
        return retry_on_deadlock(
            lambda: self._record_review_once(order_id, conclusion, recomendaciones, now)
        )

    def _record_review_once(
        self,
        order_id: str,
        conclusion: str,
        recomendaciones: str | None,
        now: datetime,
    ) -> str:
        moment = _utc_naive(now)
        with self._engine.begin() as conn:
            state = _lock_order(conn, order_id)
            if state != "COMPLETADA":
                return "NOT_COMPLETED"
            existing = conn.execute(
                text(
                    "SELECT 1 FROM revisiones_laboratorio "
                    "WHERE orden_id = :o AND estado = 'COMPLETADA'"
                ),
                {"o": order_id},
            ).first()
            if existing is not None:
                return "ALREADY_REVIEWED"
            medico = conn.execute(
                text("SELECT medico_id FROM ordenes_laboratorio WHERE id = :o"), {"o": order_id}
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO revisiones_laboratorio (id, orden_id, medico_id, estado, "
                    "conclusion, recomendaciones, fecha_revision) "
                    "VALUES (:id, :o, :m, 'COMPLETADA', :conclusion, :rec, :ahora)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "o": order_id,
                    "m": medico,
                    "conclusion": conclusion,
                    "rec": recomendaciones,
                    "ahora": moment,
                },
            )
            _outbox(
                conn, order_id, "revision_laboratorio.registrada", {"estado": "COMPLETADA"}, moment
            )
        return "CREATED"

    # ---------- documents ----------

    def create_document(self, command: NewDocument) -> StoredDocument:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO documentos_clinicos (id, paciente_id, orden_id, subido_por, "
                    "tipo_documento, nombre_archivo, proveedor_storage, ruta_storage, tipo_mime, "
                    "hash_sha256, tamano_bytes, estado, clasificacion, version_documento, "
                    "creado_en) "
                    "VALUES (:id, :p, :o, :u, :tipo, :nombre, 'LOCAL_PRIVADO', :ruta, :mime, "
                    ":hash, :tamano, 'DISPONIBLE', 'CONFIDENCIAL', 1, SYSUTCDATETIME())"
                ),
                {
                    "id": command.document_id,
                    "p": command.paciente_id,
                    "o": command.orden_id,
                    "u": command.subido_por_user_id,
                    "tipo": command.tipo_documento,
                    "nombre": command.nombre_archivo,
                    "ruta": command.ruta_storage,
                    "mime": command.tipo_mime,
                    "hash": command.hash_sha256,
                    "tamano": command.tamano_bytes,
                },
            )
        document = self.get_document(command.document_id)
        assert document is not None
        return document

    def get_document(self, document_id: str) -> StoredDocument | None:
        guid = _as_guid(document_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT d.id, d.orden_id, d.nombre_archivo, d.ruta_storage, d.hash_sha256, "
                    "d.tipo_mime, d.tamano_bytes, pa.id AS paciente_id, "
                    "pa.usuario_id AS paciente_user_id, "
                    "me.usuario_id AS medico_user_id "
                    "FROM documentos_clinicos d "
                    "JOIN pacientes pa ON pa.id = d.paciente_id "
                    "LEFT JOIN ordenes_laboratorio o ON o.id = d.orden_id "
                    "LEFT JOIN medicos me ON me.id = o.medico_id WHERE d.id = :d"
                ),
                {"d": guid},
            ).first()
        if row is None:
            return None
        return StoredDocument(
            id=_canonical(row.id),
            paciente_id=_canonical(row.paciente_id),
            paciente_user_id=_canonical(row.paciente_user_id),
            orden_id=None if row.orden_id is None else _canonical(row.orden_id),
            medico_user_id=None if row.medico_user_id is None else _canonical(row.medico_user_id),
            nombre_archivo=row.nombre_archivo,
            ruta_storage=row.ruta_storage,
            hash_sha256=row.hash_sha256,
            tipo_mime=row.tipo_mime,
            tamano_bytes=row.tamano_bytes,
        )

    # ---------- audit ----------

    def record_audit(self, event: AuditEvent) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO auditoria_accesos (id, usuario_id, paciente_id, entidad, "
                    "entidad_id, accion, fecha_evento, resultado, correlation_id) "
                    "VALUES (:id, :u, :p, :entidad, :entidad_id, :accion, :ahora, :resultado, "
                    ":correlacion)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": event.actor_user_id,
                    "p": event.paciente_id,
                    "entidad": event.entidad,
                    "entidad_id": event.entidad_id,
                    "accion": event.accion,
                    "ahora": _utc_naive(event.occurred_at),
                    "resultado": event.resultado,
                    "correlacion": _as_guid(event.correlation_id) if event.correlation_id else None,
                },
            )


def _lock_order(conn: Connection, order_id: str) -> str:
    """Lock the order row for the rest of the transaction; return its current state."""
    state = conn.execute(
        text("SELECT estado FROM ordenes_laboratorio WITH (UPDLOCK, ROWLOCK) WHERE id = :o"),
        {"o": order_id},
    ).scalar()
    return state


def _outbox(conn: Connection, aggregate_id: str, event: str, payload: dict, now: datetime) -> None:
    """Append an outbox event with the next version for this order (locked by the caller)."""
    version = conn.execute(
        text(
            "SELECT ISNULL(MAX(version_agregado), 0) + 1 FROM eventos_outbox "
            "WHERE agregado_tipo = 'orden_laboratorio' AND agregado_id = :a"
        ),
        {"a": aggregate_id},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, payload, "
            "version_agregado, creado_en) "
            "VALUES (:id, 'orden_laboratorio', :a, :tipo, :payload, :version, :ahora)"
        ),
        {
            "id": str(uuid.uuid4()),
            "a": aggregate_id,
            "tipo": event,
            "payload": json.dumps(payload),
            "version": version,
            "ahora": now,
        },
    )

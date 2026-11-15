"""SQL Server adapter for the laboratory ports (E3).

Every state change locks the order row (UPDLOCK) first, so two concurrent writers cannot
both complete a detail, both review, or both assign the same outbox version. Results are
only inserted, never updated: a correction would be a new row, not a rewrite. Parameterized
SQL only.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.laboratory.application.ports import (
    AuditEvent,
    LabDetail,
    LabOrder,
    LabOrderSummary,
    LabResult,
    LabTestType,
    NewDocument,
    NewOrder,
    PatientSummary,
    ReportParties,
    ResultEntry,
    StoredDocument,
)
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the API uses lower case."""
    return str(uuid.UUID(str(value)))


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _optional_canonical(value: object) -> str | None:
    return None if value is None else _canonical(value)


def _as_guid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


# A patient is treated by a doctor when a cita (any status) or a lab order links them. The
# predicate expects the alias p for pacientes and the bound parameter :medico for the doctor.
_TREATED_BY_DOCTOR = (
    "(EXISTS (SELECT 1 FROM citas c "
    "JOIN horarios_disponibles h ON h.id = c.horario_id "
    "JOIN agendas a ON a.id = h.agenda_id "
    "WHERE c.paciente_id = p.id AND a.medico_id = :medico) "
    "OR EXISTS (SELECT 1 FROM ordenes_laboratorio o "
    "WHERE o.paciente_id = p.id AND o.medico_id = :medico))"
)


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

    def patient_is_related(self, medico_id: str, paciente_id: str) -> bool:
        medico, paciente = _as_guid(medico_id), _as_guid(paciente_id)
        if medico is None or paciente is None:
            return False
        # Only the constant predicate is concatenated; every value is a bound parameter.
        query = "SELECT 1 FROM pacientes p WHERE p.id = :paciente AND " + _TREATED_BY_DOCTOR  # noqa: S608
        with self._engine.connect() as conn:
            found = conn.execute(text(query), {"paciente": paciente, "medico": medico}).first()
        return found is not None

    def patient_account_is_active(self, paciente_id: str) -> bool:
        guid = _as_guid(paciente_id)
        if guid is None:
            return False
        with self._engine.connect() as conn:
            estado = conn.execute(
                text(
                    "SELECT u.estado FROM pacientes p "
                    "JOIN usuarios u ON u.id = p.usuario_id WHERE p.id = :paciente"
                ),
                {"paciente": guid},
            ).scalar()
        return estado == "ACTIVO"

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
                    "prioridad, estado, observaciones, origen) "
                    "VALUES (:id, :p, :m, :ahora, :prioridad, 'SOLICITADA', :obs, :origen)"
                ),
                {
                    "id": command.order_id,
                    "p": command.paciente_id,
                    "m": command.medico_id,
                    "ahora": now,
                    "prioridad": command.prioridad,
                    "obs": command.observaciones,
                    "origen": command.origen,
                },
            )
            detalles = []
            for test_id in command.tipo_prueba_ids:
                detalle_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, "
                        "estado) VALUES (:id, :o, :t, 'PENDIENTE')"
                    ),
                    {"id": detalle_id, "o": command.order_id, "t": test_id},
                )
                detalles.append(
                    {"id": detalle_id, "tipo_prueba_id": test_id, "estado": "PENDIENTE"}
                )
            # Replica snapshot: the order and its detail lines. Observations stay in SQL Server.
            snapshot = {
                "id": command.order_id,
                "paciente_id": command.paciente_id,
                "medico_id": command.medico_id,
                "origen": command.origen,
                "fecha_orden": now.isoformat(),
                "prioridad": command.prioridad,
                "estado": "SOLICITADA",
                "detalles": detalles,
            }
            _outbox(
                conn,
                command.order_id,
                "orden_laboratorio.creada",
                {"estado": "SOLICITADA", "orden": snapshot},
                now,
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
                    "me.usuario_id AS medico_user_id, o.prioridad, o.estado, o.observaciones, "
                    "o.origen FROM ordenes_laboratorio o "
                    "JOIN pacientes pa ON pa.id = o.paciente_id "
                    "LEFT JOIN medicos me ON me.id = o.medico_id WHERE o.id = :o"
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
            medico_id=_optional_canonical(header.medico_id),
            medico_user_id=_optional_canonical(header.medico_user_id),
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
            origen=header.origen,
        )

    def list_pending_orders(self, *, limit: int, offset: int) -> list[LabOrder]:
        """One page of pending orders, oldest first. The page is cut on orders, not on lines."""
        # Static statement: only the paging values are bound. Lines are grouped in Python.
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "WITH pagina AS (SELECT o.id FROM ordenes_laboratorio o "
                    "WHERE o.estado IN ('SOLICITADA', 'EN_PROCESO') "
                    "ORDER BY o.fecha_orden ASC, o.id ASC "
                    "OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY) "
                    "SELECT o.id, o.paciente_id, pa.usuario_id AS paciente_user_id, o.medico_id, "
                    "me.usuario_id AS medico_user_id, o.prioridad, o.estado, o.observaciones, "
                    "o.origen, o.fecha_orden, d.id AS detalle_id, d.tipo_prueba_id, "
                    "d.estado AS detalle_estado "
                    "FROM pagina "
                    "JOIN ordenes_laboratorio o ON o.id = pagina.id "
                    "JOIN pacientes pa ON pa.id = o.paciente_id "
                    "LEFT JOIN medicos me ON me.id = o.medico_id "
                    "JOIN ordenes_laboratorio_detalle d ON d.orden_id = o.id "
                    "ORDER BY o.fecha_orden ASC, o.id ASC, d.id ASC"
                ),
                {"skip": offset, "take": limit},
            ).all()
        orders: dict[str, LabOrder] = {}
        lines: dict[str, list[LabDetail]] = {}
        for row in rows:
            key = _canonical(row.id)
            if key not in orders:
                orders[key] = LabOrder(
                    id=key,
                    paciente_id=_canonical(row.paciente_id),
                    paciente_user_id=_canonical(row.paciente_user_id),
                    medico_id=_optional_canonical(row.medico_id),
                    medico_user_id=_optional_canonical(row.medico_user_id),
                    prioridad=row.prioridad,
                    estado=row.estado,
                    observaciones=row.observaciones,
                    details=(),
                    origen=row.origen,
                    fecha=row.fecha_orden,
                )
                lines[key] = []
            lines[key].append(
                LabDetail(
                    id=_canonical(row.detalle_id),
                    tipo_prueba_id=_canonical(row.tipo_prueba_id),
                    estado=row.detalle_estado,
                )
            )
        return [replace(order, details=tuple(lines[key])) for key, order in orders.items()]

    def list_orders(
        self,
        *,
        paciente_id: str | None,
        medico_id: str | None,
        estado: str | None,
        limit: int,
        offset: int,
    ) -> list[LabOrderSummary]:
        patient = _as_guid(paciente_id) if paciente_id is not None else None
        doctor = _as_guid(medico_id) if medico_id is not None else None
        if patient is None and doctor is None:
            return []
        # One static statement: the flags switch each optional side on or off. Values are bound.
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT o.id, o.paciente_id, o.estado, o.fecha_orden, o.medico_id "
                    "FROM ordenes_laboratorio o "
                    "WHERE ((:usa_paciente = 1 AND o.paciente_id = :paciente) "
                    "OR (:usa_medico = 1 AND o.medico_id = :medico)) "
                    "AND (:filtra_estado = 0 OR o.estado = :estado) "
                    "ORDER BY o.fecha_orden DESC, o.id ASC "
                    "OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY"
                ),
                {
                    "usa_paciente": int(patient is not None),
                    "paciente": patient,
                    "usa_medico": int(doctor is not None),
                    "medico": doctor,
                    "filtra_estado": int(estado is not None),
                    "estado": estado,
                    "skip": offset,
                    "take": limit,
                },
            ).all()
        return [
            LabOrderSummary(
                id=_canonical(row.id),
                paciente_id=_canonical(row.paciente_id),
                estado=row.estado,
                fecha=row.fecha_orden,
                medico_id=_optional_canonical(row.medico_id),
            )
            for row in rows
        ]

    # ---------- patient directory and test catalog ----------

    def list_patients(self, *, medico_id: str, limit: int, offset: int) -> list[PatientSummary]:
        medico = _as_guid(medico_id)
        if medico is None:
            return []
        # Only the constant predicate is concatenated; the doctor and the paging are bound.
        query = (
            "SELECT p.id, u.nombre, u.apellido FROM pacientes p "  # noqa: S608
            "JOIN usuarios u ON u.id = p.usuario_id "
            "WHERE u.estado = 'ACTIVO' AND " + _TREATED_BY_DOCTOR + " "
            "ORDER BY u.apellido, u.nombre, p.id "
            "OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(query),
                {"medico": medico, "skip": offset, "take": limit},
            ).all()
        return [
            PatientSummary(id=_canonical(row.id), nombre=row.nombre, apellido=row.apellido)
            for row in rows
        ]

    def list_test_types(self) -> list[LabTestType]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, codigo, nombre, descripcion, unidad_predeterminada "
                    "FROM tipos_prueba_laboratorio WHERE activo = 1 ORDER BY nombre, id"
                )
            ).all()
        return [
            LabTestType(
                id=_canonical(row.id),
                codigo=row.codigo,
                nombre=row.nombre,
                descripcion=row.descripcion,
                unidad_predeterminada=row.unidad_predeterminada,
            )
            for row in rows
        ]

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
                    "SELECT estado, tipo_prueba_id FROM ordenes_laboratorio_detalle "
                    "WITH (UPDLOCK, ROWLOCK) WHERE id = :d AND orden_id = :o"
                ),
                {"d": detail_id, "o": order_id},
            ).first()
            if detail is None:
                return "DETAIL_NOT_FOUND"
            if detail.estado == "COMPLETADO":
                return "DETAIL_ALREADY_COMPLETED"
            # Analytes already reported for this test: each analyte is reported once.
            reported = {
                _canonical(row.analito_id)
                for row in conn.execute(
                    text(
                        "SELECT analito_id FROM resultados_laboratorio "
                        "WHERE detalle_orden_id = :d AND analito_id IS NOT NULL"
                    ),
                    {"d": detail_id},
                ).all()
            }
            incoming = {
                _canonical(entry.analito_id) for entry in entries if entry.analito_id is not None
            }
            if incoming & reported or len(incoming) != sum(
                1 for entry in entries if entry.analito_id is not None
            ):
                return "ANALYTE_ALREADY_REPORTED"
            projected_results: list[dict] = []
            for entry in entries:
                result_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO resultados_laboratorio (id, detalle_orden_id, analito_id, "
                        "nombre_analito, valor_texto, valor_numerico, unidad, metodo_analitico, "
                        "tipo_muestra, contexto_muestra, estado_embarazo, interpretacion, "
                        "referencia_aplicada_id, referencia_version, referencia_min, "
                        "referencia_max, validado_por, validado_en) "
                        "VALUES (:id, :d, :analito, :nombre, :texto, :numero, :unidad, :metodo, "
                        ":muestra, :contexto, :embarazo, :interpretacion, :referencia, "
                        ":referencia_version, :minimo, :maximo, :validador, :ahora)"
                    ),
                    {
                        "id": result_id,
                        "d": detail_id,
                        "analito": None if entry.analito_id is None else _as_guid(entry.analito_id),
                        "nombre": entry.nombre_analito,
                        "texto": entry.valor_texto,
                        "numero": entry.valor_numerico,
                        "unidad": entry.unidad,
                        "metodo": entry.metodo_analitico,
                        "muestra": entry.tipo_muestra,
                        "contexto": entry.contexto_muestra,
                        "embarazo": entry.estado_embarazo,
                        "interpretacion": entry.interpretacion,
                        "referencia": entry.referencia_id,
                        "referencia_version": entry.referencia_version,
                        "minimo": entry.referencia_min,
                        "maximo": entry.referencia_max,
                        "validador": actor_user_id,
                        "ahora": moment,
                    },
                )
                projected_results.append(
                    {
                        "id": result_id,
                        "nombre_analito": entry.nombre_analito,
                        "valor_texto": entry.valor_texto,
                        "valor_numerico": entry.valor_numerico,
                        "unidad": entry.unidad,
                        "referencia_min": entry.referencia_min,
                        "referencia_max": entry.referencia_max,
                        "interpretacion": entry.interpretacion,
                        "validado_en": moment.isoformat(),
                    }
                )
            # A test is complete when every mandatory analyte (obligatorio=1) has a result.
            # A test with no mandatory analyte is complete with its first submission.
            required = {
                _canonical(row.analito_id)
                for row in conn.execute(
                    text(
                        "SELECT analito_id FROM conocimiento_pruebas_analitos "
                        "WHERE tipo_prueba_id = :t AND obligatorio = 1"
                    ),
                    {"t": detail.tipo_prueba_id},
                ).all()
            }
            reported |= incoming
            complete = not required or required <= reported
            conn.execute(
                text(
                    "UPDATE ordenes_laboratorio_detalle SET estado = :estado, "
                    "fecha_resultado = CASE WHEN :estado = 'COMPLETADO' THEN :ahora "
                    "ELSE fecha_resultado END WHERE id = :d"
                ),
                {
                    "estado": "COMPLETADO" if complete else "EN_PROCESO",
                    "ahora": moment,
                    "d": detail_id,
                },
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
            detail_state = "COMPLETADO" if complete else "EN_PROCESO"
            _outbox(
                conn,
                order_id,
                "resultado_laboratorio.registrado",
                {
                    "detalle_id": detail_id,
                    "estado_orden": new_state,
                    "orden_estado": new_state,
                    "detalle": {
                        "id": detail_id,
                        "estado": detail_state,
                        "fecha_resultado": moment.isoformat() if complete else None,
                    },
                    "resultados": projected_results,
                },
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
                    "r.valor_numerico, r.unidad, r.referencia_min, r.referencia_max, "
                    "r.interpretacion FROM resultados_laboratorio r "
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
                referencia_min=None if row.referencia_min is None else float(row.referencia_min),
                referencia_max=None if row.referencia_max is None else float(row.referencia_max),
                interpretacion=row.interpretacion,
            )
            for row in rows
        ]

    def report_parties(self, order_id: str) -> ReportParties | None:
        guid = _as_guid(order_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT pa.tipo_documento, pa.numero_documento, pa.fecha_nacimiento, "
                    "pa.sexo_registrado, up.nombre AS paciente_nombre, "
                    "up.apellido AS paciente_apellido, up.email, up.telefono, "
                    "um.nombre AS medico_nombre, um.apellido AS medico_apellido, "
                    "me.registro_profesional FROM ordenes_laboratorio o "
                    "JOIN pacientes pa ON pa.id = o.paciente_id "
                    "JOIN usuarios up ON up.id = pa.usuario_id "
                    "LEFT JOIN medicos me ON me.id = o.medico_id "
                    "LEFT JOIN usuarios um ON um.id = me.usuario_id WHERE o.id = :o"
                ),
                {"o": guid},
            ).first()
        if row is None:
            return None
        document = " ".join(part for part in (row.tipo_documento, row.numero_documento) if part)
        doctor = " ".join(part for part in (row.medico_nombre, row.medico_apellido) if part)
        return ReportParties(
            patient_name=" ".join(
                part for part in (row.paciente_nombre, row.paciente_apellido) if part
            ),
            patient_document=document or None,
            patient_birth_date=row.fecha_nacimiento,
            patient_sex=row.sexo_registrado,
            patient_email=row.email,
            patient_phone=row.telefono,
            doctor_name=doctor or None,
            doctor_registration=row.registro_profesional,
        )

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

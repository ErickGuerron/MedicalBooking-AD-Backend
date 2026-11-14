"""SQL Server adapter for the catalog ports. Parameterized SQL only.

Reads expose active rows only. Doctor rows never select ``registro_profesional`` or the e-mail.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.catalog.application.ports import (
    Doctor,
    Service,
    Specialty,
    SpecialtyRef,
    WriteStatus,
)
from medicalbooking.modules.catalog.domain.policies import derive_attention_schedule
from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation

# Upcoming slots read to derive the attention schedule of the doctors.
SCHEDULE_HORIZON = timedelta(days=31)

_SERVICE_SELECT = (
    "SELECT id, nombre, duracion_min, precio_base, moneda, especialidad_id FROM servicios_medicos "
)


def _guid(value: object) -> str:
    """SQL Server returns GUIDs in upper case; the API uses lower case."""
    return str(uuid.UUID(str(value)))


def _specialty(row) -> Specialty:
    return Specialty(id=row.id, nombre=row.nombre, descripcion=row.descripcion)


def _service(row) -> Service:
    return Service(
        id=_guid(row.id),
        nombre=row.nombre,
        duracion_min=row.duracion_min,
        precio_base=Decimal(row.precio_base),
        moneda=row.moneda,
        especialidad_id=row.especialidad_id,
    )


class SqlCatalogStore:
    def __init__(
        self, engine: Engine, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._engine = engine
        self._clock = clock

    # ---------- reads ----------

    def list_specialties(self) -> list[Specialty]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, nombre, descripcion FROM especialidades "
                    "WHERE activo = 1 ORDER BY nombre, id"
                )
            ).all()
        return [_specialty(row) for row in rows]

    def list_services(self) -> list[Service]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(_SERVICE_SELECT + "WHERE activo = 1 ORDER BY nombre, id")
            ).all()
        return [_service(row) for row in rows]

    def list_doctors(self, especialidad_id: int | None) -> list[Doctor]:
        sql = (
            "SELECT m.id, m.duracion_cita_min, u.nombre, u.apellido FROM medicos m "
            "JOIN usuarios u ON u.id = m.usuario_id WHERE m.activo = 1 "
        )
        params: dict[str, object] = {}
        if especialidad_id is not None:
            sql += (
                "AND EXISTS (SELECT 1 FROM medicos_especialidades me JOIN especialidades e "
                "ON e.id = me.especialidad_id WHERE me.medico_id = m.id "
                "AND me.especialidad_id = :esp AND e.activo = 1) "
            )
            params["esp"] = especialidad_id
        sql += "ORDER BY u.apellido, u.nombre, m.id"
        with self._engine.connect() as conn:
            doctors = conn.execute(text(sql), params).all()
            ids = [str(row.id) for row in doctors]
            specialties = _specialties_of(conn, ids)
            agendas, slots = self._schedule_inputs(conn, ids)
        return [
            Doctor(
                id=_guid(row.id),
                nombre=f"{row.nombre} {row.apellido}".strip(),
                especialidades=tuple(specialties[_guid(row.id)]),
                duracion_cita_min=row.duracion_cita_min,
                horario_atencion=derive_attention_schedule(
                    agendas[_guid(row.id)], slots[_guid(row.id)]
                ),
            )
            for row in doctors
        ]

    def _schedule_inputs(self, conn: Connection, ids: list[str]):
        """Active agenda date ranges and upcoming slots in the local time of each site."""
        agendas: dict[str, list] = defaultdict(list)
        slots: dict[str, list] = defaultdict(list)
        if not ids:
            return agendas, slots
        now = self._clock().astimezone(UTC).replace(tzinfo=None)
        rows = conn.execute(
            text(
                "SELECT a.medico_id, a.fecha_inicio, a.fecha_fin FROM agendas a "
                "WHERE a.activa = 1 AND a.medico_id IN (SELECT m.id FROM medicos m "
                "WHERE m.activo = 1)"
            )
        ).all()
        for row in rows:
            agendas[_guid(row.medico_id)].append((row.fecha_inicio, row.fecha_fin))
        rows = conn.execute(
            text(
                "SELECT a.medico_id, h.inicio_utc, h.fin_utc, s.zona_horaria "
                "FROM horarios_disponibles h JOIN agendas a ON a.id = h.agenda_id "
                "JOIN consultorios c ON c.id = a.consultorio_id "
                "JOIN sedes s ON s.id = c.sede_id "
                "WHERE a.activa = 1 AND h.estado <> 'BLOQUEADO' "
                "AND h.inicio_utc >= :desde AND h.inicio_utc < :hasta"
            ),
            {"desde": now, "hasta": now + SCHEDULE_HORIZON},
        ).all()
        for row in rows:
            zone = ZoneInfo(row.zona_horaria)
            slots[_guid(row.medico_id)].append(
                (
                    row.inicio_utc.replace(tzinfo=UTC).astimezone(zone).replace(tzinfo=None),
                    row.fin_utc.replace(tzinfo=UTC).astimezone(zone).replace(tzinfo=None),
                )
            )
        return agendas, slots

    # ---------- writes ----------

    def create_specialty(self, nombre, descripcion):
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO especialidades (nombre, descripcion) "
                        "OUTPUT inserted.id, inserted.nombre, inserted.descripcion "
                        "VALUES (:n, :d)"
                    ),
                    {"n": nombre, "d": descripcion},
                ).one()
        except IntegrityError as error:
            if is_unique_violation(str(error.orig)):
                return WriteStatus.DUPLICATE_NAME, None
            raise
        return WriteStatus.OK, _specialty(row)

    def update_specialty(self, especialidad_id, changes):
        columns = {"nombre": "nombre", "descripcion": "descripcion", "activo": "activo"}
        sets = [f"{columns[key]} = :{key}" for key in changes if key in columns]
        try:
            with self._engine.begin() as conn:
                updated = conn.execute(
                    text(f"UPDATE especialidades SET {', '.join(sets)} WHERE id = :id"),  # noqa: S608
                    {**changes, "id": especialidad_id},
                ).rowcount
                if updated != 1:
                    return WriteStatus.NOT_FOUND, None
                row = conn.execute(
                    text("SELECT id, nombre, descripcion FROM especialidades WHERE id = :id"),
                    {"id": especialidad_id},
                ).one()
        except IntegrityError as error:
            if is_unique_violation(str(error.orig)):
                return WriteStatus.DUPLICATE_NAME, None
            raise
        return WriteStatus.OK, _specialty(row)

    def set_doctor_specialties(self, medico_id, especialidad_ids):
        with self._engine.begin() as conn:
            if (
                conn.execute(text("SELECT 1 FROM medicos WHERE id = :m"), {"m": medico_id}).first()
                is None
            ):
                return WriteStatus.NOT_FOUND, []
            if especialidad_ids and _count_active(conn, especialidad_ids) != len(especialidad_ids):
                return WriteStatus.UNKNOWN_SPECIALTY, []
            conn.execute(
                text("DELETE FROM medicos_especialidades WHERE medico_id = :m"), {"m": medico_id}
            )
            for especialidad_id in especialidad_ids:
                conn.execute(
                    text(
                        "INSERT INTO medicos_especialidades (medico_id, especialidad_id) "
                        "VALUES (:m, :e)"
                    ),
                    {"m": medico_id, "e": especialidad_id},
                )
            refs = _specialties_of(conn, [medico_id])[_guid(medico_id)]
        return WriteStatus.OK, refs

    def create_service(self, nombre, duracion_min, precio_base, moneda, especialidad_id):
        with self._engine.begin() as conn:
            if especialidad_id is not None and _count_active(conn, [especialidad_id]) != 1:
                return WriteStatus.UNKNOWN_SPECIALTY, None
            servicio_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO servicios_medicos (id, especialidad_id, nombre, duracion_min, "
                    "precio_base, moneda) VALUES (:id, :e, :n, :d, :p, :m)"
                ),
                {
                    "id": servicio_id,
                    "e": especialidad_id,
                    "n": nombre,
                    "d": duracion_min,
                    "p": precio_base,
                    "m": moneda,
                },
            )
            row = conn.execute(text(_SERVICE_SELECT + "WHERE id = :id"), {"id": servicio_id}).one()
        return WriteStatus.OK, _service(row)

    def set_service_specialty(self, servicio_id, especialidad_id):
        with self._engine.begin() as conn:
            if especialidad_id is not None and _count_active(conn, [especialidad_id]) != 1:
                return WriteStatus.UNKNOWN_SPECIALTY, None
            updated = conn.execute(
                text("UPDATE servicios_medicos SET especialidad_id = :e WHERE id = :s"),
                {"e": especialidad_id, "s": servicio_id},
            ).rowcount
            if updated != 1:
                return WriteStatus.NOT_FOUND, None
            row = conn.execute(text(_SERVICE_SELECT + "WHERE id = :id"), {"id": servicio_id}).one()
        return WriteStatus.OK, _service(row)

    def record_audit(self, actor_user_id, entidad, entidad_id, accion, detalle) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO auditoria_accesos (id, usuario_id, entidad, entidad_id, accion, "
                    "fecha_evento, resultado, motivo_acceso) "
                    "VALUES (:id, :u, :entidad, :entidad_id, :accion, :ahora, 'OK', :detalle)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": actor_user_id,
                    "entidad": entidad,
                    "entidad_id": entidad_id,
                    "accion": accion,
                    "ahora": self._clock().astimezone(UTC).replace(tzinfo=None),
                    "detalle": detalle,
                },
            )


def _count_active(conn: Connection, ids: list[int]) -> int:
    marks = ", ".join(f":e{index}" for index in range(len(ids)))
    params = {f"e{index}": value for index, value in enumerate(ids)}
    return conn.execute(
        text(f"SELECT COUNT(*) FROM especialidades WHERE activo = 1 AND id IN ({marks})"),  # noqa: S608
        params,
    ).scalar_one()


def _specialties_of(conn: Connection, medico_ids: list[str]) -> dict[str, list[SpecialtyRef]]:
    """Active specialties per doctor id (canonical GUID keys; every requested id is present)."""
    result: dict[str, list[SpecialtyRef]] = {_guid(medico_id): [] for medico_id in medico_ids}
    if not medico_ids:
        return result
    rows = conn.execute(
        text(
            "SELECT me.medico_id, e.id, e.nombre FROM medicos_especialidades me "
            "JOIN especialidades e ON e.id = me.especialidad_id WHERE e.activo = 1 "
            "ORDER BY e.nombre, e.id"
        )
    ).all()
    for row in rows:
        key = _guid(row.medico_id)
        if key in result:
            result[key].append(SpecialtyRef(id=row.id, nombre=row.nombre))
    return result

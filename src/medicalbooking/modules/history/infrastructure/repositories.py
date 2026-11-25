"""SQL Server adapter for the clinical history port. Parameterized SQL only.

Table and column names come from the whitelists in the domain module, never from a request. Every
state change is a conditional UPDATE on the stored estado, so two concurrent changes cannot both
apply. Profile lookups and the audit writer are delegated to the laboratory adapter, as the other
clinical modules do. historia_eventos is append-only in the database itself (migration 0045).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, text

from medicalbooking.modules.history.application.ports import CitaOwnership, Entry, VitalSigns
from medicalbooking.modules.history.domain.policies import (
    ALERGIA,
    ANTECEDENTE,
    MEDICACION,
    PROBLEMA,
    TABLE_BY_KIND,
    VACUNA,
)
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore

_KINDS = {
    "alergia": ALERGIA,
    "antecedente": ANTECEDENTE,
    "medicacion": MEDICACION,
    "problema": PROBLEMA,
    "vacuna": VACUNA,
}

# The value columns of each table, besides the shared ones (id, paciente_id, estado, origen, ...).
VALUE_COLUMNS: dict[str, tuple[str, ...]] = {
    "alergia": ("sustancia", "categoria", "reaccion", "severidad"),
    "antecedente": ("tipo", "descripcion", "parentesco", "cie11"),
    "medicacion": ("medicamento", "dosis", "frecuencia", "inicio"),
    "problema": ("descripcion", "cie11", "inicio"),
    "vacuna": ("vacuna", "dosis_numero", "fecha", "lote"),
}
VITAL_COLUMNS = (
    "peso_kg",
    "talla_cm",
    "pa_sistolica",
    "pa_diastolica",
    "frecuencia_cardiaca",
    "temperatura_c",
    "saturacion",
)
_SHARED = (
    "id",
    "paciente_id",
    "origen",
    "verificada",
    "verificada_por",
    "verificada_en",
    "registrada_por",
    "registrada_en",
)
_STATE_COLUMN = "estado"


def _canonical(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _as_guid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


class SqlHistoryStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._shared = SqlLabStore(engine)

    # ---------- identities and relations ----------

    def resolve_patient_id(self, user_id: str) -> str | None:
        return self._shared.resolve_patient_id(user_id)

    def resolve_medico_id(self, user_id: str) -> str | None:
        return self._shared.resolve_medico_id(user_id)

    def doctor_treats_patient(self, medico_id: str, paciente_id: str) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT TOP 1 1 FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id "
                    "WHERE a.medico_id = :m AND c.paciente_id = :p"
                ),
                {"m": medico_id, "p": paciente_id},
            ).first()
        return found is not None

    def get_cita(self, cita_id: str) -> CitaOwnership | None:
        guid = _as_guid(cita_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT c.id, c.paciente_id, a.medico_id FROM citas c "
                    "JOIN horarios_disponibles h ON h.id = c.horario_id "
                    "JOIN agendas a ON a.id = h.agenda_id WHERE c.id = :c"
                ),
                {"c": guid},
            ).first()
        if row is None:
            return None
        return CitaOwnership(
            cita_id=_canonical(row.id),
            paciente_id=_canonical(row.paciente_id),
            medico_id=_canonical(row.medico_id),
        )

    # ---------- entries ----------

    def insert_entry(
        self,
        kind: str,
        entry_id: str,
        paciente_id: str,
        values: dict[str, Any],
        *,
        origen: str,
        verificada: bool,
        verificada_por: str | None,
        registrada_por: str,
        now: datetime,
    ) -> None:
        table = TABLE_BY_KIND[kind]
        columns = [
            "id",
            "paciente_id",
            _STATE_COLUMN,
            "origen",
            "verificada",
            "verificada_por",
            "verificada_en",
            "registrada_por",
            "registrada_en",
            *VALUE_COLUMNS[kind],
        ]
        params: dict[str, Any] = {
            "id": entry_id,
            "paciente_id": paciente_id,
            _STATE_COLUMN: _KINDS[kind].initial,
            "origen": origen,
            "verificada": int(verificada),
            "verificada_por": verificada_por,
            "verificada_en": _utc_naive(now) if verificada else None,
            "registrada_por": registrada_por,
            "registrada_en": _utc_naive(now),
            **{column: values.get(column) for column in VALUE_COLUMNS[kind]},
        }
        placeholders = ", ".join(f":{column}" for column in columns)
        # Table and column names are whitelisted above; every value is bound as a parameter.
        statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
        with self._engine.begin() as conn:
            conn.execute(text(statement), params)

    def get_entry(self, kind: str, entry_id: str) -> Entry | None:
        guid = _as_guid(entry_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            row = conn.execute(text(self._select(kind) + " WHERE id = :id"), {"id": guid}).first()
        return self._entry(kind, row) if row is not None else None

    def list_entries(self, kind: str, paciente_id: str) -> list[Entry]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(self._select(kind) + " WHERE paciente_id = :p ORDER BY registrada_en DESC"),
                {"p": paciente_id},
            ).all()
        return [self._entry(kind, row) for row in rows]

    def verify_entry(self, kind: str, entry_id: str, medico_user_id: str, now: datetime) -> bool:
        table = TABLE_BY_KIND[kind]
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"UPDATE {table} SET verificada = 1, verificada_por = :u, verificada_en = :n "  # noqa: S608
                    "WHERE id = :id AND verificada = 0"
                ),
                {"u": medico_user_id, "n": _utc_naive(now), "id": entry_id},
            )
        return result.rowcount == 1

    def change_estado(
        self, kind: str, entry_id: str, expected: str, target: str, now: datetime
    ) -> bool:
        table = TABLE_BY_KIND[kind]
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"UPDATE {table} SET {_STATE_COLUMN} = :target, actualizada_en = :n "  # noqa: S608
                    f"WHERE id = :id AND {_STATE_COLUMN} = :expected"
                ),
                {"target": target, "n": _utc_naive(now), "id": entry_id, "expected": expected},
            )
        return result.rowcount == 1

    # ---------- vital signs ----------

    def insert_vitals(
        self,
        vitals_id: str,
        paciente_id: str,
        cita_id: str,
        values: dict[str, Any],
        *,
        registrada_por: str,
        now: datetime,
        corrige_a: str | None = None,
        motivo_correccion: str | None = None,
    ) -> None:
        columns = [
            "id",
            "paciente_id",
            "cita_id",
            "corrige_a",
            "motivo_correccion",
            "registrada_por",
            "registrada_en",
            *VITAL_COLUMNS,
        ]
        params = {
            "id": vitals_id,
            "paciente_id": paciente_id,
            "cita_id": cita_id,
            "corrige_a": corrige_a,
            "motivo_correccion": motivo_correccion,
            "registrada_por": registrada_por,
            "registrada_en": _utc_naive(now),
            **{column: values.get(column) for column in VITAL_COLUMNS},
        }
        placeholders = ", ".join(f":{column}" for column in columns)
        statement = (
            f"INSERT INTO historia_signos_vitales ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
        )
        with self._engine.begin() as conn:
            conn.execute(text(statement), params)

    def get_vitals(self, vitals_id: str) -> VitalSigns | None:
        guid = _as_guid(vitals_id)
        if guid is None:
            return None
        columns = ", ".join(
            (
                "id",
                "paciente_id",
                "cita_id",
                "corrige_a",
                "motivo_correccion",
                "registrada_por",
                "registrada_en",
                *VITAL_COLUMNS,
            )
        )
        with self._engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT {columns} FROM historia_signos_vitales WHERE id = :id"),  # noqa: S608
                {"id": guid},
            ).first()
        return self._vitals(row) if row is not None else None

    def list_vitals(self, paciente_id: str) -> list[VitalSigns]:
        columns = ", ".join(
            (
                "id",
                "paciente_id",
                "cita_id",
                "corrige_a",
                "motivo_correccion",
                "registrada_por",
                "registrada_en",
                *VITAL_COLUMNS,
            )
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {columns} FROM historia_signos_vitales "  # noqa: S608
                    "WHERE paciente_id = :p ORDER BY registrada_en DESC"
                ),
                {"p": paciente_id},
            ).all()
        return [self._vitals(row) for row in rows]

    # ---------- event log and audit ----------

    def append_event(
        self,
        *,
        paciente_id: str,
        entidad: str,
        entidad_id: str,
        accion: str,
        actor_user_id: str,
        motivo: str | None,
        antes: str | None,
        despues: str | None,
        now: datetime,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO historia_eventos (paciente_id, entidad, entidad_id, accion, "
                    "actor_usuario_id, motivo, antes, despues, ocurrido_en) VALUES (:p, :e, :i, "
                    ":a, :u, :m, :antes, :despues, :n)"
                ),
                {
                    "p": paciente_id,
                    "e": entidad,
                    "i": entidad_id,
                    "a": accion,
                    "u": actor_user_id,
                    "m": motivo,
                    "antes": antes,
                    "despues": despues,
                    "n": _utc_naive(now),
                },
            )

    def record_audit(self, event: AuditEvent) -> None:
        self._shared.record_audit(event)

    # ---------- helpers ----------

    def _select(self, kind: str) -> str:
        columns = ", ".join((*_SHARED, _STATE_COLUMN, *VALUE_COLUMNS[kind]))
        return f"SELECT {columns} FROM {TABLE_BY_KIND[kind]}"  # noqa: S608

    def _vitals(self, row: Any) -> VitalSigns:
        return VitalSigns(
            id=_canonical(row.id),
            paciente_id=_canonical(row.paciente_id),
            cita_id=_canonical(row.cita_id),
            corrige_a=_canonical(row.corrige_a) if row.corrige_a else None,
            motivo_correccion=row.motivo_correccion,
            registrada_por=_canonical(row.registrada_por),
            registrada_en=row.registrada_en,
            values={column: getattr(row, column) for column in VITAL_COLUMNS},
        )

    def _entry(self, kind: str, row: Any) -> Entry:
        return Entry(
            id=_canonical(row.id),
            kind=kind,
            paciente_id=_canonical(row.paciente_id),
            estado=getattr(row, _STATE_COLUMN),
            origen=row.origen,
            verificada=bool(row.verificada),
            verificada_por=_canonical(row.verificada_por) if row.verificada_por else None,
            verificada_en=row.verificada_en,
            registrada_por=_canonical(row.registrada_por),
            registrada_en=row.registrada_en,
            data={column: _json_value(getattr(row, column)) for column in VALUE_COLUMNS[kind]},
        )


__all__ = ["SqlHistoryStore"]

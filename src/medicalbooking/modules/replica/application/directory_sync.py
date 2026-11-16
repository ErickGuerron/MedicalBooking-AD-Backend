"""Doctors, specialties and services for the read-only screens of the replica.

Each table is replaced as a whole inside one transaction, like the authorization catalog: a
failed run leaves the previous directory in place. The appointment's doctor is set per row, so
an appointment keeps the doctor it had until the next sync reaches it.

Only staff names and catalog text cross this boundary. No patient data is ever part of it.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DirectorySnapshot:
    especialidades: tuple[tuple[int, str, bool], ...]
    medicos: tuple[tuple[str, str, str, bool], ...]
    # (id, especialidad_id, nombre, duracion_min, precio_base, moneda, activo,
    #  requiere_orden_medica, requiere_toma_sangre)
    servicios: tuple[tuple[str, int | None, str, int, str, str, bool, bool, bool], ...]
    citas_medico: tuple[tuple[str, str], ...]


class _Executor(Protocol):
    def execute(self, sql: str, params: Any = None) -> object: ...


class DirectoryTarget(Protocol):
    def transaction(self) -> AbstractContextManager[_Executor]: ...


_DELETE_SERVICIOS = "DELETE FROM replica.servicios"
_DELETE_MEDICOS = "DELETE FROM replica.medicos"
_DELETE_ESPECIALIDADES = "DELETE FROM replica.especialidades"
_INSERT_ESPECIALIDAD = "INSERT INTO replica.especialidades (id, nombre, activo) VALUES (%s, %s, %s)"
_INSERT_MEDICO = (
    "INSERT INTO replica.medicos (id, nombre, apellido, activo) VALUES (%s, %s, %s, %s)"
)
_INSERT_SERVICIO = (
    "INSERT INTO replica.servicios (id, especialidad_id, nombre, duracion_min, precio_base, "
    "moneda, activo, requiere_orden_medica, requiere_toma_sangre) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
_SET_CITA_MEDICO = "UPDATE replica.citas SET medico_id = %s WHERE id = %s"


def replace_directory(snapshot: DirectorySnapshot, target: DirectoryTarget) -> None:
    with target.transaction() as conn:
        conn.execute(_DELETE_SERVICIOS)
        conn.execute(_DELETE_MEDICOS)
        conn.execute(_DELETE_ESPECIALIDADES)
        for especialidad in snapshot.especialidades:
            conn.execute(_INSERT_ESPECIALIDAD, especialidad)
        for medico in snapshot.medicos:
            conn.execute(_INSERT_MEDICO, medico)
        for servicio in snapshot.servicios:
            conn.execute(_INSERT_SERVICIO, servicio)
        for cita_id, medico_id in snapshot.citas_medico:
            conn.execute(_SET_CITA_MEDICO, (medico_id, cita_id))

"""The read-only directory in MongoDB Atlas: doctors, specialties and services.

The three collections are replaced inside one transaction, like the Supabase directory, so a
failed run leaves the previous directory in place. Each appointment document receives its doctor
by appointment id. Only staff names and catalog text are written: no patient data.
"""

from __future__ import annotations

from typing import Any

from medicalbooking.modules.replica.application.directory_sync import DirectorySnapshot

MEDICOS = "medicos"
ESPECIALIDADES = "especialidades"
SERVICIOS = "servicios"
CITAS = "citas"


def replace_directory_mongo(snapshot: DirectorySnapshot, database: Any) -> None:
    with database.client.start_session() as session:
        with session.start_transaction():
            database[SERVICIOS].delete_many({}, session=session)
            database[MEDICOS].delete_many({}, session=session)
            database[ESPECIALIDADES].delete_many({}, session=session)
            if snapshot.especialidades:
                database[ESPECIALIDADES].insert_many(
                    [
                        {"_id": especialidad_id, "nombre": nombre, "activo": activo}
                        for especialidad_id, nombre, activo in snapshot.especialidades
                    ],
                    session=session,
                )
            if snapshot.medicos:
                database[MEDICOS].insert_many(
                    [
                        {"_id": medico_id, "nombre": nombre, "apellido": apellido, "activo": activo}
                        for medico_id, nombre, apellido, activo in snapshot.medicos
                    ],
                    session=session,
                )
            if snapshot.servicios:
                database[SERVICIOS].insert_many(
                    [
                        {
                            "_id": servicio_id,
                            "especialidad_id": especialidad_id,
                            "nombre": nombre,
                            "duracion_min": duracion_min,
                            "precio_base": precio_base,
                            "moneda": moneda,
                            "activo": activo,
                            "requiere_orden_medica": requiere_orden_medica,
                            "servicio_adicional_id": servicio_adicional_id,
                        }
                        for (
                            servicio_id,
                            especialidad_id,
                            nombre,
                            duracion_min,
                            precio_base,
                            moneda,
                            activo,
                            requiere_orden_medica,
                            servicio_adicional_id,
                        ) in snapshot.servicios
                    ],
                    session=session,
                )
            for cita_id, medico_id in snapshot.citas_medico:
                database[CITAS].update_one(
                    {"_id": cita_id},
                    {"$set": {"medico_id": medico_id}},
                    session=session,
                )

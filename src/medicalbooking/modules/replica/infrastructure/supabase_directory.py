"""Read-only directory source (SQL Server): doctors, specialties, services, appointment doctors.

Reads only what the replica needs for its screens. Patient names, document numbers, the
appointment's free text and any clinical data are never selected here.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from medicalbooking.modules.replica.application.directory_sync import DirectorySnapshot


def read_directory(engine: Engine) -> DirectorySnapshot:
    with engine.connect() as conn:
        especialidades = tuple(
            (int(row.id), row.nombre, bool(row.activo))
            for row in conn.execute(
                text("SELECT id, nombre, activo FROM especialidades ORDER BY id")
            ).all()
        )
        medicos = tuple(
            (str(row.id), row.nombre, row.apellido, bool(row.activo))
            for row in conn.execute(
                text(
                    "SELECT m.id, u.nombre, u.apellido, m.activo FROM medicos AS m "
                    "JOIN usuarios AS u ON u.id = m.usuario_id ORDER BY m.id"
                )
            ).all()
        )
        servicios = tuple(
            (
                str(row.id),
                int(row.especialidad_id) if row.especialidad_id is not None else None,
                row.nombre,
                int(row.duracion_min),
                str(row.precio_base),
                row.moneda.strip(),
                bool(row.activo),
            )
            for row in conn.execute(
                text(
                    "SELECT id, especialidad_id, nombre, duracion_min, precio_base, moneda, "
                    "activo FROM servicios_medicos ORDER BY id"
                )
            ).all()
        )
        citas_medico = tuple(
            (str(row.cita_id), str(row.medico_id))
            for row in conn.execute(
                text(
                    "SELECT c.id AS cita_id, a.medico_id FROM citas AS c "
                    "JOIN horarios_disponibles AS h ON h.id = c.horario_id "
                    "JOIN agendas AS a ON a.id = h.agenda_id"
                )
            ).all()
        )
    return DirectorySnapshot(
        especialidades=especialidades,
        medicos=medicos,
        servicios=servicios,
        citas_medico=citas_medico,
    )

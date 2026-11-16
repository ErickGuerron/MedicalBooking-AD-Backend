"""Seed scenario for medical consultations, overdue appointments, and lab orders."""

from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import text
from medicalbooking.app import create_app

def main() -> None:
    app = create_app()
    engine = app.extensions["medicalbooking.engine"]

    with engine.begin() as conn:
        medico_id = conn.execute(
            text("SELECT m.id FROM medicos m JOIN usuarios u ON u.id = m.usuario_id WHERE u.email = 'medico1@test.com'")
        ).scalar()
        agenda_id = conn.execute(
            text("SELECT id FROM agendas WHERE medico_id = :m"), {"m": medico_id}
        ).scalar()
        servicio_id = conn.execute(
            text("SELECT TOP 1 id FROM servicios_medicos WHERE nombre LIKE '%Consulta%'")
        ).scalar()

        # Patients
        nico_id = conn.execute(
            text("SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id WHERE u.email = 'mabejake22@gmail.com'")
        ).scalar()
        pac1_id = conn.execute(
            text("SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id WHERE u.email = 'paciente1@test.com'")
        ).scalar()
        pac2_id = conn.execute(
            text("SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id WHERE u.email = 'paciente2@test.com'")
        ).scalar()
        ana_id = conn.execute(
            text("SELECT p.id FROM pacientes p JOIN usuarios u ON u.id = p.usuario_id WHERE u.email = 'e2e-e1539e48@test.com'")
        ).scalar()

        # Lab tests
        hemo_id = conn.execute(
            text("SELECT id FROM tipos_prueba_laboratorio WHERE codigo = 'HEMO-SINT'")
        ).scalar()
        gluc_id = conn.execute(
            text("SELECT id FROM tipos_prueba_laboratorio WHERE codigo = 'GLUC-SINT'")
        ).scalar()
        lipi_id = conn.execute(
            text("SELECT id FROM tipos_prueba_laboratorio WHERE codigo = 'LIPI-SINT'")
        ).scalar()

        # 1. Citas médicas para Medico Uno
        citas_data = [
            # Slot past, CONFIRMADA (overdue/elapsed today)
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                datetime(2026, 11, 16, 14, 0),
                datetime(2026, 11, 16, 14, 30),
                "CONFIRMADA",
                nico_id,
                "Control clínico general (Horario concluido)",
            ),
            # Slot past, COMPLETADA
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                datetime(2026, 11, 16, 15, 0),
                datetime(2026, 11, 16, 15, 30),
                "COMPLETADA",
                pac1_id,
                "Consulta médica realizada con éxito",
            ),
            # Slot past, NO_ASISTIO
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                datetime(2026, 11, 16, 16, 0),
                datetime(2026, 11, 16, 16, 30),
                "NO_ASISTIO",
                pac2_id,
                "Paciente no se presentó a su consulta",
            ),
            # Slot future, CONFIRMADA (upcoming tomorrow)
            (
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                datetime(2026, 11, 17, 15, 0),
                datetime(2026, 11, 17, 15, 30),
                "CONFIRMADA",
                ana_id,
                "Dolor abdominal recurrente (Cita próxima)",
            ),
        ]

        for slot_id, cita_id, start, end, estado, pac_id, motivo in citas_data:
            conn.execute(
                text(
                    "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc, estado) "
                    "VALUES (:id, :a, :i, :f, 'RESERVADO')"
                ),
                {"id": slot_id, "a": agenda_id, "i": start, "f": end},
            )
            conn.execute(
                text(
                    "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, motivo, "
                    "reservada_en, confirmada_en, clave_idempotencia) "
                    "VALUES (:id, :p, :h, :s, :e, :m, :r, :c, :k)"
                ),
                {
                    "id": cita_id,
                    "p": pac_id,
                    "h": slot_id,
                    "s": servicio_id,
                    "e": estado,
                    "m": motivo,
                    "r": start,
                    "c": start,
                    "k": f"idem-{cita_id}",
                },
            )
            print(f"Cita creada: {estado} ({start} - {end}) para paciente {pac_id}")

        # 2. Órdenes de laboratorio
        # Orden SOLICITADA para Nico Paz
        ord1_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, prioridad, estado, observaciones, origen) "
                "VALUES (:id, :p, :m, SYSUTCDATETIME(), 'NORMAL', 'SOLICITADA', 'Chequeo de rutina solicitado por médico', 'MEDICO')"
            ),
            {"id": ord1_id, "p": nico_id, "m": medico_id},
        )
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, estado) "
                "VALUES (:id, :o, :t, 'PENDIENTE')"
            ),
            {"id": str(uuid.uuid4()), "o": ord1_id, "t": hemo_id},
        )
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, estado) "
                "VALUES (:id, :o, :t, 'PENDIENTE')"
            ),
            {"id": str(uuid.uuid4()), "o": ord1_id, "t": gluc_id},
        )
        print(f"Orden SOLICITADA creada: {ord1_id} para Nico Paz")

        # Orden EN_PROCESO para Paciente Uno
        ord2_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden, prioridad, estado, observaciones, origen) "
                "VALUES (:id, :p, :m, SYSUTCDATETIME(), 'URGENTE', 'EN_PROCESO', 'Muestra tomada en laboratorio. En procesamiento.', 'MEDICO')"
            ),
            {"id": ord2_id, "p": pac1_id, "m": medico_id},
        )
        det2_id = str(uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio_detalle (id, orden_id, tipo_prueba_id, estado, fecha_toma_muestra) "
                "VALUES (:id, :o, :t, 'EN_PROCESO', SYSUTCDATETIME())"
            ),
            {"id": det2_id, "o": ord2_id, "t": lipi_id},
        )
        print(f"Orden EN_PROCESO creada: {ord2_id} para Paciente Uno")

    print("Escenario de pruebas sembrado exitosamente.")

if __name__ == "__main__":
    main()

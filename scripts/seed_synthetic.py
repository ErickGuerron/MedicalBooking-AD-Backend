"""Create synthetic development accounts. Idempotent; never runs in production.

    SEED_PASSWORD='<at least 12 chars>' uv run python scripts/seed_synthetic.py

The password comes from the environment and is never stored in this repository.
All accounts use the .test domain and fictitious names.
"""

from __future__ import annotations

import os
import sys
import uuid

from sqlalchemy import text

from medicalbooking.config import Settings
from medicalbooking.modules.identity.application.ports import NewUser
from medicalbooking.modules.identity.application.use_cases import MIN_PASSWORD_LENGTH
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher
from medicalbooking.shared.infrastructure.database import create_database_engine

PATIENT = "paciente1@synthetic.test"
LAB = "laboratorista1@synthetic.test"
DOCTOR_A = "medico1@synthetic.test"
DOCTOR_B = "medico2@synthetic.test"
ADMIN = "admin1@synthetic.test"


def main() -> int:
    settings = Settings()
    if settings.app_env == "production":
        print("Refusing to seed synthetic accounts in production.", file=sys.stderr)
        return 2
    password = os.environ.get("SEED_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Set SEED_PASSWORD with at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 2

    engine = create_database_engine(settings)
    directory = SqlUserDirectory(engine)
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash(password)

    for email, role, first, last in (
        (PATIENT, "PACIENTE", "Paciente", "Sintetico"),
        (LAB, "LABORATORISTA", "Laboratorista", "Sintetico"),
        (ADMIN, "ADMINISTRADOR", "Administrador", "Sintetico"),
    ):
        if _exists(engine, email):
            _set_password(engine, email, password_hash)
            continue
        directory.create_user(
            NewUser(
                user_id=str(uuid.uuid4()),
                email=email,
                first_name=first,
                last_name=last,
                password_hash=password_hash,
                roles=(role,),
            )
        )
        print(f"created {role} {email}")

    for email, first, registry in (
        (DOCTOR_A, "Medico", "SYN-MED-0001"),
        (DOCTOR_B, "Medico", "SYN-MED-0002"),
    ):
        if _exists(engine, email):
            _set_password(engine, email, password_hash)
            continue
        _create_doctor(engine, email, first, registry, password_hash)
        print(f"created MEDICO {email}")

    _grant_access(engine, DOCTOR_A, PATIENT)
    print("synthetic accounts ready; passwords come from SEED_PASSWORD and are not stored")
    return 0


def _set_password(engine, email: str, password_hash: str) -> None:
    """Rotate the password of a synthetic account. Only the fixed .test accounts above."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE credenciales_usuario "
                "SET password_hash = :h, actualizado_en = SYSUTCDATETIME() "
                "WHERE usuario_id = (SELECT id FROM usuarios WHERE email = :e)"
            ),
            {"h": password_hash, "e": email},
        )


def _exists(engine, email: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT 1 FROM usuarios WHERE email = :e"), {"e": email}).first()
            is not None
        )


def _create_doctor(engine, email: str, first: str, registry: str, password_hash: str) -> None:
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
                "VALUES (:id, :e, :n, N'Sintetico', SYSUTCDATETIME(), SYSUTCDATETIME())"
            ),
            {"id": user_id, "e": email, "n": first},
        )
        conn.execute(
            text(
                "INSERT INTO credenciales_usuario (usuario_id, password_hash, actualizado_en) "
                "VALUES (:id, :h, SYSUTCDATETIME())"
            ),
            {"id": user_id, "h": password_hash},
        )
        role_id = conn.execute(text("SELECT id FROM roles WHERE nombre = 'MEDICO'")).scalar_one()
        conn.execute(
            text("INSERT INTO usuarios_roles (usuario_id, rol_id) VALUES (:u, :r)"),
            {"u": user_id, "r": role_id},
        )
        conn.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "VALUES (NEWID(), :u, :reg)"
            ),
            {"u": user_id, "reg": registry},
        )


def _grant_access(engine, doctor_email: str, patient_email: str) -> None:
    """Give the first doctor a current consent-based grant over the synthetic patient."""
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM accesos_paciente ap "
                "JOIN medicos m ON m.id = ap.medico_id "
                "JOIN usuarios du ON du.id = m.usuario_id "
                "JOIN pacientes p ON p.id = ap.paciente_id "
                "JOIN usuarios pu ON pu.id = p.usuario_id "
                "WHERE du.email = :d AND pu.email = :p AND ap.revocado_en IS NULL"
            ),
            {"d": doctor_email, "p": patient_email},
        ).first()
        if exists:
            return
        conn.execute(
            text(
                "INSERT INTO accesos_paciente (id, paciente_id, medico_id, alcance, fundamento, "
                "otorgado_en, autorizado_por) "
                "SELECT NEWID(), p.id, m.id, 'RESULTADOS_LABORATORIO', 'CONSENTIMIENTO', "
                "SYSUTCDATETIME(), p.usuario_id "
                "FROM pacientes p "
                "JOIN usuarios pu ON pu.id = p.usuario_id, medicos m "
                "JOIN usuarios du ON du.id = m.usuario_id "
                "WHERE pu.email = :p AND du.email = :d"
            ),
            {"p": patient_email, "d": doctor_email},
        )


if __name__ == "__main__":
    raise SystemExit(main())

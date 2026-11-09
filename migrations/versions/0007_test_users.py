"""Shared test accounts for every role, with one known password.

Revision ID: 0007_test_users
Revises: 0006_clinical_knowledge_seed

The accounts exist so that every developer and both frontends can sign in the same way
(``paciente1@test.com`` / ``Password1``). A known password must never exist in a real
environment, so the migration does nothing when ``APP_ENV=production``. It is idempotent:
an account that already exists is left untouched.
"""

from __future__ import annotations

import uuid

from alembic import op
from sqlalchemy import text

from medicalbooking.config import Settings
from medicalbooking.modules.identity.infrastructure.security import Argon2PasswordHasher

revision = "0007_test_users"
down_revision = "0006_clinical_knowledge_seed"
branch_labels = None
depends_on = None

# Deliberately public: the accounts are never created in production (see upgrade()).
PATIENT_PASSWORD = "Password1"  # noqa: S105
STAFF_PASSWORD = "Password123"  # noqa: S105
EMAIL_DOMAIN = "test.com"

# (local part, role, first name, last name, professional registry for doctors, password)
ACCOUNTS = (
    ("paciente1", "PACIENTE", "Paciente", "Uno", None, PATIENT_PASSWORD),
    ("paciente2", "PACIENTE", "Paciente", "Dos", None, PATIENT_PASSWORD),
    ("laboratorista1", "LABORATORISTA", "Laboratorista", "Uno", None, PATIENT_PASSWORD),
    ("medico1", "MEDICO", "Medico", "Uno", "TEST-MED-0001", STAFF_PASSWORD),
    ("medico2", "MEDICO", "Medico", "Dos", "TEST-MED-0002", PATIENT_PASSWORD),
    ("administrador1", "ADMINISTRADOR", "Administrador", "Uno", None, STAFF_PASSWORD),
)


_DELETE_BY_USER = (
    "DELETE FROM pacientes WHERE usuario_id = :u",
    "DELETE FROM medicos WHERE usuario_id = :u",
    "DELETE FROM usuarios_roles WHERE usuario_id = :u",
    "DELETE FROM credenciales_usuario WHERE usuario_id = :u",
)


def upgrade() -> None:
    if Settings().app_env == "production":
        return
    connection = op.get_bind()
    hasher = Argon2PasswordHasher()
    hashes = {password: hasher.hash(password) for password in {a[5] for a in ACCOUNTS}}
    for local, role, first, last, registry, password in ACCOUNTS:
        email = f"{local}@{EMAIL_DOMAIN}"
        exists = connection.execute(
            text("SELECT 1 FROM usuarios WHERE email = :e"), {"e": email}
        ).first()
        if exists:
            continue
        _create_account(connection, email, role, first, last, registry, hashes[password])
    _grant_doctor_access(connection, f"medico1@{EMAIL_DOMAIN}", f"paciente1@{EMAIL_DOMAIN}")


def _create_account(connection, email, role, first, last, registry, password_hash) -> None:
    user_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
            "VALUES (:id, :e, :n, :a, SYSUTCDATETIME(), SYSUTCDATETIME())"
        ),
        {"id": user_id, "e": email, "n": first, "a": last},
    )
    connection.execute(
        text(
            "INSERT INTO credenciales_usuario (usuario_id, password_hash, actualizado_en) "
            "VALUES (:id, :h, SYSUTCDATETIME())"
        ),
        {"id": user_id, "h": password_hash},
    )
    connection.execute(
        text(
            "INSERT INTO usuarios_roles (usuario_id, rol_id) "
            "SELECT :u, id FROM roles WHERE nombre = :r"
        ),
        {"u": user_id, "r": role},
    )
    if role == "PACIENTE":
        connection.execute(
            text(
                "INSERT INTO pacientes (id, usuario_id, creado_en) "
                "VALUES (NEWID(), :u, SYSUTCDATETIME())"
            ),
            {"u": user_id},
        )
    if role == "MEDICO":
        connection.execute(
            text(
                "INSERT INTO medicos (id, usuario_id, registro_profesional) "
                "VALUES (NEWID(), :u, :reg)"
            ),
            {"u": user_id, "reg": registry},
        )


def _grant_doctor_access(connection, doctor_email: str, patient_email: str) -> None:
    """Consent-based grant so the first doctor can read the first patient's results."""
    connection.execute(
        text(
            "INSERT INTO accesos_paciente (id, paciente_id, medico_id, alcance, fundamento, "
            "otorgado_en, autorizado_por) "
            "SELECT NEWID(), p.id, m.id, 'RESULTADOS_LABORATORIO', 'CONSENTIMIENTO', "
            "SYSUTCDATETIME(), p.usuario_id "
            "FROM pacientes p JOIN usuarios pu ON pu.id = p.usuario_id, "
            "medicos m JOIN usuarios du ON du.id = m.usuario_id "
            "WHERE pu.email = :p AND du.email = :d "
            "AND NOT EXISTS (SELECT 1 FROM accesos_paciente a "
            "WHERE a.paciente_id = p.id AND a.medico_id = m.id AND a.revocado_en IS NULL)"
        ),
        {"p": patient_email, "d": doctor_email},
    )


def downgrade() -> None:
    # Only the accounts this migration created, matched by their fixed addresses. Anything an
    # account produced (appointments, orders) would block the delete, which is the safe outcome.
    emails = tuple(f"{local}@{EMAIL_DOMAIN}" for local, *_ in ACCOUNTS)
    connection = op.get_bind()
    for email in emails:
        user = connection.execute(
            text("SELECT id FROM usuarios WHERE email = :e"), {"e": email}
        ).scalar()
        if user is None:
            continue
        params = {"u": user}
        connection.execute(
            text(
                "DELETE FROM accesos_paciente WHERE paciente_id IN "
                "(SELECT id FROM pacientes WHERE usuario_id = :u) OR medico_id IN "
                "(SELECT id FROM medicos WHERE usuario_id = :u)"
            ),
            params,
        )
        for statement in _DELETE_BY_USER:
            connection.execute(text(statement), params)
        connection.execute(text("DELETE FROM usuarios WHERE id = :u"), params)

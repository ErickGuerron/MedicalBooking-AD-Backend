"""SQL Server adapter for the identity ports. Parameterized SQL only."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    CredentialRecord,
    NewUser,
)
from medicalbooking.shared.domain.errors import Conflict, ValidationFailed


def _canonical(value: object) -> str:
    """SQL Server returns GUIDs in upper case; tokens carry lower case. Compare canonically."""
    return str(uuid.UUID(str(value)))


class SqlUserDirectory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def find_credentials_by_email(self, email: str) -> CredentialRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT u.id, u.estado, c.password_hash "
                    "FROM usuarios u "
                    "LEFT JOIN credenciales_usuario c ON c.usuario_id = u.id "
                    "WHERE u.email = :email"
                ),
                {"email": email},
            ).first()
        if row is None:
            return None
        return CredentialRecord(
            user_id=_canonical(row.id),
            email=email,
            status=row.estado,
            password_hash=row.password_hash,
        )

    def load_access(self, user_id: str) -> AccessProfile | None:
        with self._engine.connect() as conn:
            account = conn.execute(
                text("SELECT estado FROM usuarios WHERE id = :id"), {"id": user_id}
            ).first()
            if account is None:
                return None
            # .all() drains each result before the next query: ODBC rejects a second
            # statement while a lazy result is still open on the same connection.
            roles = (
                conn.execute(
                    text(
                        "SELECT r.nombre FROM usuarios_roles ur "
                        "JOIN roles r ON r.id = ur.rol_id WHERE ur.usuario_id = :id"
                    ),
                    {"id": user_id},
                )
                .scalars()
                .all()
            )
            permissions = (
                conn.execute(
                    text(
                        "SELECT DISTINCT p.codigo FROM usuarios_roles ur "
                        "JOIN roles_permisos rp ON rp.rol_id = ur.rol_id "
                        "JOIN permisos p ON p.id = rp.permiso_id "
                        "WHERE ur.usuario_id = :id"
                    ),
                    {"id": user_id},
                )
                .scalars()
                .all()
            )
            return AccessProfile(
                user_id=user_id,
                status=account.estado,
                roles=frozenset(roles),
                permissions=frozenset(permissions),
            )

    def find_patient_owner(self, patient_id: str) -> str | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                text("SELECT usuario_id FROM pacientes WHERE id = :id"),
                {"id": patient_id},
            ).scalar()
        return None if value is None else _canonical(value)

    def has_active_patient_access(self, clinician_user_id: str, patient_id: str) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                text(
                    "SELECT 1 FROM accesos_paciente ap "
                    "JOIN medicos m ON m.id = ap.medico_id "
                    "WHERE m.usuario_id = :clinician AND ap.paciente_id = :patient "
                    "AND ap.revocado_en IS NULL "
                    "AND (ap.expira_en IS NULL OR ap.expira_en > SYSUTCDATETIME())"
                ),
                {"clinician": clinician_user_id, "patient": patient_id},
            ).first()
        return found is not None

    def create_user(self, new_user: NewUser) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO usuarios "
                        "(id, email, nombre, apellido, creado_en, actualizado_en) "
                        "VALUES (:id, :email, :nombre, :apellido, "
                        "SYSUTCDATETIME(), SYSUTCDATETIME())"
                    ),
                    {
                        "id": new_user.user_id,
                        "email": new_user.email,
                        "nombre": new_user.first_name,
                        "apellido": new_user.last_name,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO credenciales_usuario "
                        "(usuario_id, password_hash, actualizado_en) "
                        "VALUES (:id, :hash, SYSUTCDATETIME())"
                    ),
                    {"id": new_user.user_id, "hash": new_user.password_hash},
                )
                for role in new_user.roles:
                    role_id = conn.execute(
                        text("SELECT id FROM roles WHERE nombre = :nombre"), {"nombre": role}
                    ).scalar()
                    if role_id is None:
                        raise ValidationFailed(f"Unknown role: {role}")
                    conn.execute(
                        text("INSERT INTO usuarios_roles (usuario_id, rol_id) VALUES (:u, :r)"),
                        {"u": new_user.user_id, "r": role_id},
                    )
                    if role == "PACIENTE":
                        conn.execute(
                            text(
                                "INSERT INTO pacientes (id, usuario_id, creado_en) "
                                "VALUES (NEWID(), :u, SYSUTCDATETIME())"
                            ),
                            {"u": new_user.user_id},
                        )
        except IntegrityError:
            # Unique email, or a concurrent request created the same account first.
            raise Conflict("An account with this email already exists") from None

"""SQL Server adapter for the identity ports. Parameterized SQL only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.modules.identity.application.ports import (
    AccessProfile,
    CredentialRecord,
    NewUser,
    UserSummary,
)
from medicalbooking.modules.identity.domain.policies import (
    LOCK_DURATION,
    MAX_FAILED_LOGINS,
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
                    "SELECT u.id, u.estado, u.bloqueado_hasta, c.password_hash, "
                    "c.intentos_fallidos "
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
            failed_attempts=row.intentos_fallidos or 0,
            locked_until=row.bloqueado_hasta.replace(tzinfo=UTC) if row.bloqueado_hasta else None,
        )

    def register_failed_login(self, user_id: str, now: datetime) -> None:
        """Mirror of policies.failure_outcome, applied atomically in SQL.

        The counter update and the lock update run in one transaction, so concurrent
        failures for one account are all counted.
        """
        at = now.astimezone(UTC).replace(tzinfo=None)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE c SET c.intentos_fallidos = CASE "
                    "WHEN u.bloqueado_hasta IS NOT NULL AND u.bloqueado_hasta <= :ahora "
                    "THEN 1 ELSE c.intentos_fallidos + 1 END "
                    "FROM credenciales_usuario c JOIN usuarios u ON u.id = c.usuario_id "
                    "WHERE c.usuario_id = :u"
                ),
                {"ahora": at, "u": user_id},
            )
            # A new window opened above (expired lock) must not keep the stale timestamp,
            # otherwise every later failure would restart the count at one.
            conn.execute(
                text(
                    "UPDATE usuarios SET bloqueado_hasta = NULL "
                    "WHERE id = :u AND bloqueado_hasta IS NOT NULL AND bloqueado_hasta <= :ahora"
                ),
                {"ahora": at, "u": user_id},
            )
            conn.execute(
                text(
                    "UPDATE u SET u.bloqueado_hasta = :hasta "
                    "FROM usuarios u JOIN credenciales_usuario c ON c.usuario_id = u.id "
                    "WHERE u.id = :u AND c.intentos_fallidos >= :maximo"
                ),
                {
                    "hasta": at + LOCK_DURATION,
                    "u": user_id,
                    "maximo": MAX_FAILED_LOGINS,
                },
            )

    def reset_failed_logins(self, user_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE credenciales_usuario SET intentos_fallidos = 0 "
                    "WHERE usuario_id = :u AND intentos_fallidos <> 0"
                ),
                {"u": user_id},
            )
            conn.execute(
                text(
                    "UPDATE usuarios SET bloqueado_hasta = NULL "
                    "WHERE id = :u AND bloqueado_hasta IS NOT NULL"
                ),
                {"u": user_id},
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
        self._create(new_user, verification=None)

    def list_users(self, limit: int, offset: int) -> list[UserSummary]:
        """One page ordered by email. Credentials are never selected.

        Role names come from the fixed roles catalogue and contain no commas, so
        STRING_AGG keeps the page to a single round trip.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT u.id, u.email, u.nombre, u.apellido, u.estado, "
                    "(SELECT STRING_AGG(r.nombre, ',') FROM usuarios_roles ur "
                    "JOIN roles r ON r.id = ur.rol_id WHERE ur.usuario_id = u.id) AS roles "
                    "FROM usuarios u ORDER BY u.email "
                    "OFFSET :saltar ROWS FETCH NEXT :cantidad ROWS ONLY"
                ),
                {"saltar": offset, "cantidad": limit},
            ).all()
        return [
            UserSummary(
                user_id=_canonical(row.id),
                email=row.email,
                first_name=row.nombre,
                last_name=row.apellido,
                status=row.estado,
                roles=tuple(sorted(name for name in (row.roles or "").split(",") if name)),
            )
            for row in rows
        ]

    def create_pending_patient(self, new_user: NewUser, token_hash: str, valid_hours: int) -> None:
        self._create(new_user, verification=(token_hash, valid_hours))

    def consume_verification(self, token_hash: str) -> bool:
        """Activate the account behind a valid token, marking the token used atomically."""
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT id, usuario_id FROM verificaciones_correo WITH (UPDLOCK, ROWLOCK) "
                    "WHERE token_hash = :hash AND usado_en IS NULL "
                    "AND expira_en > SYSUTCDATETIME()"
                ),
                {"hash": token_hash},
            ).first()
            if row is None:
                return False
            conn.execute(
                text("UPDATE verificaciones_correo SET usado_en = SYSUTCDATETIME() WHERE id = :id"),
                {"id": row.id},
            )
            conn.execute(
                text(
                    "UPDATE usuarios SET estado = 'ACTIVO', actualizado_en = SYSUTCDATETIME() "
                    "WHERE id = :u"
                ),
                {"u": row.usuario_id},
            )
            return True

    def _create(self, new_user: NewUser, verification: tuple[str, int] | None) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO usuarios "
                        "(id, email, nombre, apellido, estado, creado_en, actualizado_en) "
                        "VALUES (:id, :email, :nombre, :apellido, :estado, "
                        "SYSUTCDATETIME(), SYSUTCDATETIME())"
                    ),
                    {
                        "id": new_user.user_id,
                        "email": new_user.email,
                        "nombre": new_user.first_name,
                        "apellido": new_user.last_name,
                        "estado": new_user.status,
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
                if verification is not None:
                    token_hash, valid_hours = verification
                    conn.execute(
                        text(
                            "INSERT INTO verificaciones_correo "
                            "(id, usuario_id, token_hash, creado_en, expira_en) "
                            "VALUES (NEWID(), :u, :hash, SYSUTCDATETIME(), "
                            "DATEADD(HOUR, :horas, SYSUTCDATETIME()))"
                        ),
                        {"u": new_user.user_id, "hash": token_hash, "horas": valid_hours},
                    )
        except IntegrityError:
            # Unique email, or a concurrent request created the same account first.
            raise Conflict("An account with this email already exists") from None

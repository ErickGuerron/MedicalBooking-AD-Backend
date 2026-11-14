"""Notification store against SQL Server: idempotent insert, leased claims, state transitions.

These tests commit real rows (not the rolled-back ``db`` fixture) because the claims run in
their own transactions and must be visible across connections. Every row is removed on teardown.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.notifications.application.ports import NewNotification
from medicalbooking.modules.notifications.infrastructure.repositories import SqlNotificationStore

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def store(migrated_engine: Engine) -> SqlNotificationStore:
    return SqlNotificationStore(migrated_engine)


@pytest.fixture
def patient(migrated_engine: Engine) -> Iterator[dict[str, str]]:
    """A committed patient user with an outbox event and a lab order, removed on teardown."""
    user, patient_id, doctor_user, doctor_id, order, event = (_new_id() for _ in range(6))
    email = f"{user}@example.test"
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
                "VALUES (:id, :email, N'Ana', N'Paciente', SYSUTCDATETIME(), SYSUTCDATETIME())"
            ),
            {"id": user, "email": email},
        )
        conn.execute(
            text(
                "INSERT INTO pacientes (id, usuario_id, creado_en) "
                "VALUES (:id, :u, SYSUTCDATETIME())"
            ),
            {"id": patient_id, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
                "VALUES (:id, :email, N'Doc', N'Medico', SYSUTCDATETIME(), SYSUTCDATETIME())"
            ),
            {"id": doctor_user, "email": f"{doctor_user}@example.test"},
        )
        conn.execute(
            text("INSERT INTO medicos (id, usuario_id, registro_profesional) VALUES (:id, :u, :r)"),
            {"id": doctor_id, "u": doctor_user, "r": f"REG-{doctor_id}"},
        )
        conn.execute(
            text(
                "INSERT INTO ordenes_laboratorio (id, paciente_id, medico_id, fecha_orden) "
                "VALUES (:id, :p, :m, SYSUTCDATETIME())"
            ),
            {"id": order, "p": patient_id, "m": doctor_id},
        )
        conn.execute(
            text(
                "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, payload, "
                "version_agregado, creado_en) VALUES (:id, 'orden_laboratorio', :a, "
                "'resultado_laboratorio.registrado', '{}', 1, SYSUTCDATETIME())"
            ),
            {"id": event, "a": order},
        )
    created = {"user": user, "email": email, "order": order, "event": event, "doctor": doctor_user}
    yield created
    _cleanup(migrated_engine, created, patient_id, doctor_id)


def _cleanup(engine: Engine, created: dict[str, str], patient_id: str, doctor_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM notificaciones WHERE usuario_id = :u"), {"u": created["user"]}
        )
        conn.execute(text("DELETE FROM eventos_outbox WHERE id = :e"), {"e": created["event"]})
        conn.execute(text("DELETE FROM ordenes_laboratorio WHERE id = :o"), {"o": created["order"]})
        conn.execute(text("DELETE FROM medicos WHERE id = :m"), {"m": doctor_id})
        conn.execute(text("DELETE FROM pacientes WHERE id = :p"), {"p": patient_id})
        conn.execute(
            text("DELETE FROM usuarios WHERE id IN (:u, :d)"),
            {"u": created["user"], "d": created["doctor"]},
        )


def _notification(patient: dict[str, str], key: str | None = None) -> NewNotification:
    return NewNotification(
        key=key or f"{patient['event']}:RESULTADO_LABORATORIO",
        event_id=uuid.UUID(patient["event"]),
        usuario_id=patient["user"],
        tipo="RESULTADO_LABORATORIO",
        destinatario=patient["email"],
    )


def _count(engine: Engine, key: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM notificaciones WHERE clave_idempotencia = :k"), {"k": key}
        ).scalar_one()


def test_enqueue_twice_with_the_same_key_creates_one_row(
    store: SqlNotificationStore, migrated_engine: Engine, patient: dict[str, str]
) -> None:
    notification = _notification(patient)

    first = store.enqueue(notification, NOW)
    second = store.enqueue(notification, NOW)

    assert first is True
    assert second is False
    assert _count(migrated_engine, notification.key) == 1


def test_find_patient_contact_resolves_the_lab_order_patient(
    store: SqlNotificationStore, patient: dict[str, str]
) -> None:
    contact = store.find_patient_contact("orden_laboratorio", uuid.UUID(patient["order"]))

    assert contact is not None
    assert contact.user_id == patient["user"]
    assert contact.email == patient["email"]


def test_find_patient_contact_is_none_for_an_unknown_aggregate(
    store: SqlNotificationStore, patient: dict[str, str]
) -> None:
    assert store.find_patient_contact("cita", uuid.uuid4()) is None


def test_a_second_claim_does_not_get_a_row_leased_by_the_first(
    store: SqlNotificationStore, patient: dict[str, str]
) -> None:
    store.enqueue(_notification(patient), NOW)

    first = store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)
    second = store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)

    assert len(first) == 1
    assert second == []


def test_a_leased_row_becomes_due_again_when_the_lease_expires(
    store: SqlNotificationStore, patient: dict[str, str]
) -> None:
    store.enqueue(_notification(patient), NOW)
    store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)

    reclaimed = store.claim_due(NOW + timedelta(minutes=6), NOW + timedelta(minutes=11), limit=10)

    assert len(reclaimed) == 1


def test_concurrent_claims_never_take_the_same_row(
    store: SqlNotificationStore, patient: dict[str, str]
) -> None:
    store.enqueue(_notification(patient), NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(store.claim_due, NOW, NOW + timedelta(minutes=5), 1) for _ in range(2)
        ]
        results = [future.result() for future in futures]

    claimed_ids = [row.id for batch in results for row in batch]
    assert len(claimed_ids) == 1


def test_mark_sent_records_the_provider_id_and_clears_the_error(
    store: SqlNotificationStore, migrated_engine: Engine, patient: dict[str, str]
) -> None:
    notification = _notification(patient)
    store.enqueue(notification, NOW)
    row = store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)[0]

    store.mark_sent(row.id, "brevo-message-1", NOW)

    with migrated_engine.connect() as conn:
        state = conn.execute(
            text(
                "SELECT estado, numero_intentos, id_mensaje_externo, ultimo_error_codigo "
                "FROM notificaciones WHERE clave_idempotencia = :k"
            ),
            {"k": notification.key},
        ).one()
    assert (state.estado, state.numero_intentos) == ("ENVIADA", 1)
    assert state.id_mensaje_externo == "brevo-message-1"
    assert state.ultimo_error_codigo is None


def test_failed_attempt_stores_only_the_code_and_schedules_the_retry(
    store: SqlNotificationStore, migrated_engine: Engine, patient: dict[str, str]
) -> None:
    notification = _notification(patient)
    store.enqueue(notification, NOW)
    row = store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)[0]
    retry_at = NOW + timedelta(minutes=1)

    store.mark_attempt_failed(row.id, 1, "BREVO_HTTP_503", retry_at, NOW)

    with migrated_engine.connect() as conn:
        state = conn.execute(
            text(
                "SELECT estado, numero_intentos, ultimo_error_codigo, siguiente_intento_en "
                "FROM notificaciones WHERE clave_idempotencia = :k"
            ),
            {"k": notification.key},
        ).one()
    assert (state.estado, state.numero_intentos) == ("PENDIENTE", 1)
    assert state.ultimo_error_codigo == "BREVO_HTTP_503"
    assert state.siguiente_intento_en == retry_at.replace(tzinfo=None)


def test_last_failed_attempt_marks_the_row_fallida(
    store: SqlNotificationStore, migrated_engine: Engine, patient: dict[str, str]
) -> None:
    notification = _notification(patient)
    store.enqueue(notification, NOW)
    row = store.claim_due(NOW, NOW + timedelta(minutes=5), limit=10)[0]

    store.mark_attempt_failed(row.id, 5, "RuntimeError", None, NOW)

    with migrated_engine.connect() as conn:
        estado = conn.execute(
            text("SELECT estado FROM notificaciones WHERE clave_idempotencia = :k"),
            {"k": notification.key},
        ).scalar_one()
    assert estado == "FALLIDA"

"""A confirmed payment confirms its appointment, on both paths, in the same transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.domain.webhook import interpret_event
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.repositories import SqlPaymentsStore
from tests.integration.test_stripe_webhook_store import _event, _seed_pago

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _cita(conn, ids: dict[str, str], estado: str) -> str:
    """An appointment in ``estado`` linked to the seeded invoice."""
    cita_id = str(uuid.uuid4())
    user, medico, sede, consultorio, agenda, servicio, slot = (str(uuid.uuid4()) for _ in range(7))
    conn.execute(
        text(
            "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
            "VALUES (:id, :e, N'Doc', N'Tor', SYSUTCDATETIME(), SYSUTCDATETIME())"
        ),
        {"id": user, "e": f"{user}@example.test"},
    )
    conn.execute(
        text("INSERT INTO medicos (id, usuario_id, registro_profesional) VALUES (:m, :u, :r)"),
        {"m": medico, "u": user, "r": f"REG-{user}"},
    )
    conn.execute(text("INSERT INTO sedes (id, nombre) VALUES (:s, N'Sede')"), {"s": sede})
    conn.execute(
        text("INSERT INTO consultorios (id, sede_id, nombre) VALUES (:c, :s, N'C1')"),
        {"c": consultorio, "s": sede},
    )
    conn.execute(
        text(
            "INSERT INTO agendas (id, medico_id, consultorio_id, fecha_inicio) "
            "VALUES (:a, :m, :c, CAST(SYSUTCDATETIME() AS date))"
        ),
        {"a": agenda, "m": medico, "c": consultorio},
    )
    start = (datetime.now(UTC) + timedelta(days=3)).replace(tzinfo=None)
    conn.execute(
        text(
            "INSERT INTO horarios_disponibles (id, agenda_id, inicio_utc, fin_utc, estado) "
            "VALUES (:h, :a, :i, DATEADD(minute, 30, :i), 'RESERVADO')"
        ),
        {"h": slot, "a": agenda, "i": start},
    )
    conn.execute(
        text(
            "INSERT INTO servicios_medicos (id, nombre, duracion_min, precio_base) "
            "VALUES (:s, N'Consulta', 30, 50.00)"
        ),
        {"s": servicio},
    )
    conn.execute(
        text(
            "INSERT INTO citas (id, paciente_id, horario_id, servicio_id, estado, reservada_en, "
            "clave_idempotencia) SELECT :c, f.paciente_id, :h, :s, :estado, SYSUTCDATETIME(), :k "
            "FROM facturas f WHERE f.id = :f"
        ),
        {
            "c": cita_id,
            "h": slot,
            "s": servicio,
            "estado": estado,
            "k": f"k-{cita_id}",
            "f": ids["factura"],
        },
    )
    conn.execute(
        text("UPDATE facturas SET cita_id = :c WHERE id = :f"), {"c": cita_id, "f": ids["factura"]}
    )
    return cita_id


def _cita_state(engine: Engine, cita_id: str) -> tuple[str, object, int, int]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT estado, confirmada_en FROM citas WHERE id = :c"), {"c": cita_id}
        ).one()
        history = conn.execute(
            text(
                "SELECT COUNT(*) FROM historial_estados_cita "
                "WHERE cita_id = :c AND estado_anterior = 'PENDIENTE_PAGO' "
                "AND estado_nuevo = 'CONFIRMADA'"
            ),
            {"c": cita_id},
        ).scalar_one()
        outbox = conn.execute(
            text(
                "SELECT COUNT(*) FROM eventos_outbox WHERE agregado_tipo = 'cita' "
                "AND agregado_id = :c AND tipo_evento = 'cita.actualizada'"
            ),
            {"c": cita_id},
        ).scalar_one()
    return row.estado, row.confirmada_en, history, outbox


def _seed(engine: Engine, estado: str) -> tuple[dict[str, str], str, str]:
    session = f"cs_test_{uuid.uuid4().hex}"
    with engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
        return ids, session, _cita(conn, ids, estado)


def _pay_by_webhook(engine: Engine, session: str) -> str:
    event = _event("checkout.session.completed", session)
    return SqlPaymentsStore(engine, clock=lambda: NOW).process(
        event["id"], event["type"], interpret_event(event)
    )


def test_webhook_confirmation_confirms_the_pending_appointment(migrated_engine: Engine) -> None:
    ids, session, cita = _seed(migrated_engine, "PENDIENTE_PAGO")

    assert _pay_by_webhook(migrated_engine, session) == "PROCESADO"

    estado, confirmada_en, history, outbox = _cita_state(migrated_engine, cita)
    assert (estado, history, outbox) == ("CONFIRMADA", 1, 1)
    assert confirmada_en is not None


@pytest.mark.parametrize("estado", ["CANCELADA", "EXPIRADA"])
def test_webhook_never_resurrects_a_closed_appointment_nor_fails(
    migrated_engine: Engine, estado: str
) -> None:
    ids, session, cita = _seed(migrated_engine, estado)

    assert _pay_by_webhook(migrated_engine, session) == "PROCESADO"

    assert _cita_state(migrated_engine, cita)[0] == estado
    with migrated_engine.connect() as conn:
        pago = conn.execute(
            text("SELECT estado FROM pagos WHERE id = :p"), {"p": ids["pago"]}
        ).scalar_one()
    assert pago == "CONFIRMADO"


def test_checkout_reconciliation_confirms_the_appointment_once(migrated_engine: Engine) -> None:
    ids, _, cita = _seed(migrated_engine, "PENDIENTE_PAGO")
    store = SqlBillingStore(migrated_engine)

    store.confirm_payment(ids["pago"], ids["factura"], NOW)
    store.confirm_payment(ids["pago"], ids["factura"], NOW)

    estado, _, history, outbox = _cita_state(migrated_engine, cita)
    assert (estado, history, outbox) == ("CONFIRMADA", 1, 1)

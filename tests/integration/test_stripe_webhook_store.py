"""Stripe webhook persistence against real SQL Server (E4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.payments.domain.webhook import interpret_event
from medicalbooking.modules.payments.infrastructure.repositories import SqlPaymentsStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _id() -> str:
    return str(uuid.uuid4())


def _seed_pago(conn: Connection, *, session_id: str, monto: str = "50.00") -> dict[str, str]:
    usuario, paciente, factura, pago = _id(), _id(), _id(), _id()
    conn.execute(
        text(
            "INSERT INTO usuarios (id, email, nombre, apellido, creado_en, actualizado_en) "
            "VALUES (:id, :email, N'Nombre', N'Apellido', SYSUTCDATETIME(), SYSUTCDATETIME())"
        ),
        {"id": usuario, "email": f"{usuario}@example.test"},
    )
    conn.execute(
        text(
            "INSERT INTO pacientes (id, usuario_id, creado_en) VALUES (:id, :u, SYSUTCDATETIME())"
        ),
        {"id": paciente, "u": usuario},
    )
    conn.execute(
        text(
            "INSERT INTO facturas (id, paciente_id, subtotal, impuestos, total, moneda, estado, "
            "creada_en) VALUES (:id, :p, :m, 0, :m, 'USD', 'PENDIENTE', SYSUTCDATETIME())"
        ),
        {"id": factura, "p": paciente, "m": monto},
    )
    conn.execute(
        text(
            "INSERT INTO pagos (id, factura_id, clave_idempotencia, monto, moneda, estado, "
            "creado_en, stripe_checkout_session_id) VALUES (:id, :f, :k, :m, 'USD', 'PENDIENTE', "
            "SYSUTCDATETIME(), :s)"
        ),
        {"id": pago, "f": factura, "k": f"key-{pago}", "m": monto, "s": session_id},
    )
    return {"pago": pago, "factura": factura}


def _event(event_type: str, session_id: str, amount: int = 5000, paid: bool = True) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex}",
        "type": event_type,
        "data": {
            "object": {
                "id": session_id,
                "payment_status": "paid" if paid else "unpaid",
                "amount_total": amount,
                "currency": "usd",
            }
        },
    }


def _store(engine: Engine) -> SqlPaymentsStore:
    return SqlPaymentsStore(engine, clock=lambda: NOW)


def _process(engine: Engine, event: dict) -> str:
    return _store(engine).process(event["id"], event["type"], interpret_event(event))


def _estados(engine: Engine, ids: dict[str, str], event_id: str) -> tuple[str, str, str]:
    with engine.connect() as conn:
        pago = conn.execute(
            text("SELECT estado FROM pagos WHERE id = :id"), {"id": ids["pago"]}
        ).scalar_one()
        factura = conn.execute(
            text("SELECT estado FROM facturas WHERE id = :id"), {"id": ids["factura"]}
        ).scalar_one()
        evento = conn.execute(
            text("SELECT estado FROM stripe_eventos WHERE stripe_event_id = :e"), {"e": event_id}
        ).scalar_one()
    return pago, factura, evento


def test_paid_checkout_confirms_payment_and_invoice(migrated_engine: Engine) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    event = _event("checkout.session.completed", session)

    assert _process(migrated_engine, event) == "PROCESADO"
    assert _estados(migrated_engine, ids, event["id"]) == ("CONFIRMADO", "PAGADA", "PROCESADO")


def test_redelivered_event_is_a_duplicate_and_changes_nothing(migrated_engine: Engine) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    event = _event("checkout.session.completed", session)
    _process(migrated_engine, event)

    assert _process(migrated_engine, event) == "DUPLICADO"
    assert _estados(migrated_engine, ids, event["id"]) == ("CONFIRMADO", "PAGADA", "PROCESADO")


def test_failed_apply_rolls_back_and_redelivery_is_a_first_delivery(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    event = _event("checkout.session.completed", session)

    def fail(conn, pago, target, now):  # noqa: ANN001, ANN202
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(SqlPaymentsStore, "_apply", staticmethod(fail))
    with pytest.raises(RuntimeError):
        _process(migrated_engine, event)
    monkeypatch.undo()

    # The inbox row was rolled back too, so the retry is processed as a first delivery.
    assert _process(migrated_engine, event) == "PROCESADO"
    assert _estados(migrated_engine, ids, event["id"]) == ("CONFIRMADO", "PAGADA", "PROCESADO")


def test_event_for_unknown_session_is_ignored(migrated_engine: Engine) -> None:
    event = _event("checkout.session.completed", f"cs_unknown_{uuid.uuid4().hex}")

    assert _process(migrated_engine, event) == "IGNORADO"
    with migrated_engine.connect() as conn:
        estado = conn.execute(
            text("SELECT estado FROM stripe_eventos WHERE stripe_event_id = :e"),
            {"e": event["id"]},
        ).scalar_one()
    assert estado == "IGNORADO"


def test_amount_mismatch_fails_and_keeps_payment_pending(migrated_engine: Engine) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    event = _event("checkout.session.completed", session, amount=4999)

    assert _process(migrated_engine, event) == "FALLIDO"
    assert _estados(migrated_engine, ids, event["id"]) == ("PENDIENTE", "PENDIENTE", "FALLIDO")


def test_expired_session_cancels_pending_payment(migrated_engine: Engine) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        ids = _seed_pago(conn, session_id=session)
    event = _event("checkout.session.expired", session)

    assert _process(migrated_engine, event) == "PROCESADO"
    assert _estados(migrated_engine, ids, event["id"]) == ("CANCELADO", "PENDIENTE", "PROCESADO")


def test_decimal_amount_is_compared_in_cents(migrated_engine: Engine) -> None:
    session = f"cs_test_{uuid.uuid4().hex}"
    with migrated_engine.begin() as conn:
        _seed_pago(conn, session_id=session, monto="12.34")
    event = _event("checkout.session.completed", session, amount=1234)

    assert _process(migrated_engine, event) == "PROCESADO"
    assert Decimal("12.34") == Decimal("1234") / 100

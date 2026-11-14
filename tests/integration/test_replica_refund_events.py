"""Refunds reach the replica as one snapshot per change: reembolso.actualizado (S10).

Every change of a reembolso writes the snapshot of the refund, its payment and its invoice in the
same SQL Server transaction: the reservation (PENDIENTE), a failed provider call (FALLIDO), the
provider's answer, and a processed refund.updated webhook (PROCESADO, invoice REEMBOLSADA). A
refused reservation writes nothing. Stripe identifiers and idempotency keys are never projected.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from medicalbooking.modules.payments.domain.refunds import RefundRefused
from medicalbooking.modules.payments.infrastructure.refund_repositories import SqlRefundStore
from tests.integration.test_refund_request_api import _seed
from tests.integration.test_refund_webhook_finalization import (
    _paid_pago_with_reembolso,
    _process_refund,
    _refund_event,
)

pytestmark = pytest.mark.integration

FORBIDDEN = ("stripe", "clave")


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    return migrated_engine


def _snapshots(engine: Engine, reembolso_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT payload FROM eventos_outbox WHERE agregado_tipo = 'reembolso' "
                "AND agregado_id = :r AND tipo_evento = 'reembolso.actualizado' "
                "ORDER BY version_agregado"
            ),
            {"r": reembolso_id},
        ).all()
    return [json.loads(row.payload) for row in rows]


def _reserve(engine: Engine, pago_id: str, owner: str, refund_id: str) -> None:
    SqlRefundStore(engine).reserve(
        pago_id=pago_id,
        owner_user_id=owner,
        refund_id=refund_id,
        motivo_codigo="CANCELACION_PACIENTE",
        now=datetime.now(UTC),
        check=lambda _case: None,
    )


def test_reserving_a_refund_emits_it_as_pending(engine: Engine) -> None:
    seed = _seed(engine)
    refund_id = str(uuid.uuid4())

    _reserve(engine, seed.pago_id, seed.paciente_user, refund_id)

    [snapshot] = _snapshots(engine, refund_id)
    assert snapshot["reembolso"]["id"] == refund_id
    assert snapshot["reembolso"]["pago_id"] == seed.pago_id.lower()
    assert snapshot["reembolso"]["estado"] == "PENDIENTE"
    assert snapshot["reembolso"]["motivo_codigo"] == "CANCELACION_PACIENTE"
    assert snapshot["pago"]["id"] == seed.pago_id.lower()
    assert snapshot["factura"]["paciente_id"] == seed.paciente_id


def test_a_refused_reservation_writes_neither_the_refund_nor_its_event(engine: Engine) -> None:
    seed = _seed(engine)
    refund_id = str(uuid.uuid4())

    def refuse(_case) -> None:
        raise RefundRefused("CITA_NO_CANCELADA")

    with pytest.raises(RefundRefused):
        SqlRefundStore(engine).reserve(
            pago_id=seed.pago_id,
            owner_user_id=seed.paciente_user,
            refund_id=refund_id,
            motivo_codigo="CANCELACION_PACIENTE",
            now=datetime.now(UTC),
            check=refuse,
        )

    assert _snapshots(engine, refund_id) == []
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM reembolsos WHERE id = :r"), {"r": refund_id}
        ).scalar_one()
    assert count == 0


def test_a_failed_provider_call_emits_the_refund_as_failed(engine: Engine) -> None:
    seed = _seed(engine)
    refund_id = str(uuid.uuid4())
    _reserve(engine, seed.pago_id, seed.paciente_user, refund_id)

    SqlRefundStore(engine).mark_failed(refund_id)

    snapshots = _snapshots(engine, refund_id)
    assert [s["reembolso"]["estado"] for s in snapshots] == ["PENDIENTE", "FALLIDO"]


def test_the_provider_answer_emits_the_refund_again(engine: Engine) -> None:
    seed = _seed(engine)
    refund_id = str(uuid.uuid4())
    _reserve(engine, seed.pago_id, seed.paciente_user, refund_id)

    SqlRefundStore(engine).record_provider_answer(refund_id, "re_test_answer", "PENDIENTE")

    snapshots = _snapshots(engine, refund_id)
    assert len(snapshots) == 2
    assert snapshots[-1]["reembolso"]["estado"] == "PENDIENTE"


def test_a_processed_refund_webhook_emits_the_refund_and_the_refunded_invoice(
    migrated_engine: Engine,
) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)

    assert _process_refund(migrated_engine, _refund_event(ids["refund_id"], "succeeded")) == (
        "PROCESADO"
    )

    latest = _snapshots(migrated_engine, ids["reembolso"])[-1]
    assert latest["reembolso"]["estado"] == "PROCESADO"
    assert latest["reembolso"]["procesado_en"] is not None
    assert latest["pago"]["estado"] == "CONFIRMADO"
    assert latest["factura"]["estado"] == "REEMBOLSADA"


def test_a_replayed_refund_webhook_emits_nothing_new(migrated_engine: Engine) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    event = _refund_event(ids["refund_id"], "succeeded")
    _process_refund(migrated_engine, event)
    before = len(_snapshots(migrated_engine, ids["reembolso"]))

    assert _process_refund(migrated_engine, event) == "DUPLICADO"

    assert len(_snapshots(migrated_engine, ids["reembolso"])) == before


def test_no_stripe_identifier_or_idempotency_key_is_projected(migrated_engine: Engine) -> None:
    ids = _paid_pago_with_reembolso(migrated_engine)
    _process_refund(migrated_engine, _refund_event(ids["refund_id"], "succeeded"))

    serialized = json.dumps(_snapshots(migrated_engine, ids["reembolso"])).lower()

    for word in FORBIDDEN:
        assert word not in serialized
    assert ids["refund_id"] not in serialized

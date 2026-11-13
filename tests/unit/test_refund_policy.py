from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from medicalbooking.modules.payments.domain.refunds import (
    MAX_REQUESTS_PER_WINDOW,
    REFUND_WINDOW_DAYS,
    RefundCase,
    RefundRefused,
    check_refund_eligibility,
)

NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


def _case(**overrides) -> RefundCase:
    values = {
        "pago_estado": "CONFIRMADO",
        "monto_pagado": Decimal("25.00"),
        "pagado_en": NOW - timedelta(days=2),
        "cita_estado": "CANCELADA",
        "cancelada_en": NOW - timedelta(hours=1),
        "cita_inicio": NOW + timedelta(days=3),
        "consulta_atendida": False,
        "reembolsos_activos": (),
        "solicitudes_recientes": 0,
        "motivo_codigo": "CANCELACION_PACIENTE",
        "monto_solicitado": Decimal("25.00"),
    }
    values.update(overrides)
    return RefundCase(**values)


def test_a_cancelled_paid_appointment_is_eligible():
    check_refund_eligibility(_case(), now=NOW)


@pytest.mark.parametrize("estado", ["PENDIENTE", "RECHAZADO", "CANCELADO"])
def test_only_confirmed_payments_can_be_refunded(estado):
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(pago_estado=estado), now=NOW)
    assert raised.value.code == "PAGO_NO_CONFIRMADO"


def test_the_appointment_must_be_cancelled():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(cita_estado="CONFIRMADA"), now=NOW)
    assert raised.value.code == "CITA_NO_CANCELADA"


def test_an_attended_appointment_is_never_refunded():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(consulta_atendida=True), now=NOW)
    assert raised.value.code == "CONSULTA_ATENDIDA"


def test_a_second_active_refund_for_the_same_payment_is_refused():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(reembolsos_activos=("PENDIENTE",)), now=NOW)
    assert raised.value.code == "REEMBOLSO_EXISTENTE"


def test_a_failed_refund_does_not_block_a_new_request():
    check_refund_eligibility(_case(reembolsos_activos=("FALLIDO",)), now=NOW)


def test_only_the_full_amount_is_refundable_in_this_version():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(monto_solicitado=Decimal("10.00")), now=NOW)
    assert raised.value.code == "MONTO_NO_TOTAL"


def test_a_request_after_the_window_is_refused():
    late = NOW - timedelta(days=REFUND_WINDOW_DAYS + 1)
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(pagado_en=late), now=NOW)
    assert raised.value.code == "FUERA_DE_VENTANA"


def test_the_request_rate_limit_is_enforced():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(
            _case(solicitudes_recientes=MAX_REQUESTS_PER_WINDOW), now=NOW
        )
    assert raised.value.code == "DEMASIADAS_SOLICITUDES"


def test_the_reason_must_be_from_the_closed_catalog():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(_case(motivo_codigo="porque_quiero"), now=NOW)
    assert raised.value.code == "MOTIVO_INVALIDO"


def test_a_cancellation_after_the_appointment_started_is_not_refundable():
    with pytest.raises(RefundRefused) as raised:
        check_refund_eligibility(
            _case(cancelada_en=NOW, cita_inicio=NOW - timedelta(minutes=5)), now=NOW
        )
    assert raised.value.code == "CANCELACION_TARDIA"

"""Refund requests (payments, refunds v1).

Eligibility is decided by domain.refunds.check_refund_eligibility inside the store's transaction,
after the payment row is locked. The provider is called only after that transaction commits, so
no database transaction is open during the Stripe call. The Stripe idempotency key is the local
refund id, and a replay of the same request key returns the stored refund without a second call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.payments.application.billing_service import ProviderUnavailable
from medicalbooking.modules.payments.domain.billing import (
    PERM_CHECKOUT,
    to_cents,
    validate_idempotency_key,
)
from medicalbooking.modules.payments.domain.refunds import (
    RefundCase,
    RefundRefused,
    check_refund_eligibility,
)
from medicalbooking.shared.domain.errors import AppError, Conflict, Forbidden, NotFound

REEMBOLSO_PENDIENTE = "PENDIENTE"
logger = logging.getLogger(__name__)

REEMBOLSO_FALLIDO = "FALLIDO"
# Stripe statuses that mean the refund will not be paid. Any other status keeps the request
# pending until the refund.updated webhook settles it.
_FAILED_PROVIDER_STATUSES = frozenset({"failed", "canceled"})
_REFUND_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "medicalbooking:refund-request")


class RefundDenied(Conflict):
    """A refusal from the eligibility rules or the payment check. The detail is the code."""


class ProviderRejected(ProviderUnavailable):
    """Stripe refused the refund request explicitly (a 4xx answer). Nothing was refunded, so the
    refund is marked FALLIDO. Any other provider failure has an unknown outcome."""


class RefundProviderError(AppError):
    status = 502
    title = "Bad Gateway"


@dataclass(frozen=True)
class RefundRecord:
    id: str
    pago_id: str
    estado: str
    monto: Decimal
    motivo_codigo: str
    solicitado_en: datetime


@dataclass(frozen=True)
class RefundReservation:
    refund: RefundRecord
    created: bool
    payment_intent_id: str | None


@dataclass(frozen=True)
class RefundOutcome:
    record: RefundRecord
    replayed: bool


@dataclass(frozen=True)
class ProviderRefund:
    id: str
    status: str


class RefundStore(Protocol):
    def reserve(
        self,
        *,
        pago_id: str,
        owner_user_id: str,
        refund_id: str,
        motivo_codigo: str,
        now: datetime,
        check: Callable[[RefundCase], None],
    ) -> RefundReservation | None:
        """In one transaction: lock the payment, return the stored refund for a replay, or run
        `check` on the eligibility inputs and insert the pending refund. None when the payment
        does not exist or belongs to another patient."""
        ...

    def find_confirmed_payment_for_appointment(self, cita_id: str) -> tuple[str, str] | None:
        """(pago_id, patient_user_id) of the newest confirmed payment of the appointment."""
        ...

    def mark_failed(self, refund_id: str) -> None: ...

    def record_provider_answer(
        self, refund_id: str, stripe_refund_id: str, estado: str
    ) -> None: ...


class RefundGateway(Protocol):
    def create_refund(
        self,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
        metadata: Mapping[str, str],
    ) -> ProviderRefund: ...


def refund_request_id(pago_id: str, idempotency_key: str) -> str:
    """The refund id is derived from the payment and the request key, so a replay finds the same
    row without a dedicated key column."""
    return str(uuid.uuid5(_REFUND_ID_NAMESPACE, f"{pago_id}|{idempotency_key}"))


class RefundService:
    def __init__(
        self,
        store: RefundStore,
        gateway: RefundGateway,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._clock = clock

    def request_refund(
        self,
        actor: AccessProfile,
        *,
        pago_id: str,
        motivo_codigo: str,
        idempotency_key: str,
    ) -> RefundOutcome:
        if PERM_CHECKOUT not in actor.permissions:
            raise Forbidden("Access to this resource is not allowed")
        validate_idempotency_key(idempotency_key)
        now = self._clock()
        refund_id = refund_request_id(pago_id, idempotency_key)
        try:
            reservation = self._store.reserve(
                pago_id=pago_id,
                owner_user_id=actor.user_id,
                refund_id=refund_id,
                motivo_codigo=motivo_codigo,
                now=now,
                check=lambda case: check_refund_eligibility(case, now),
            )
        except RefundRefused as refused:
            raise RefundDenied(refused.code) from refused
        if reservation is None:
            raise NotFound("This payment does not exist")
        if not reservation.created:
            return RefundOutcome(record=reservation.refund, replayed=True)

        if reservation.payment_intent_id is None:
            # Nothing is sent: the refund cannot be identified at the provider.
            self._store.mark_failed(refund_id)
            raise RefundDenied("SIN_PAYMENT_INTENT")
        try:
            provider = self._gateway.create_refund(
                reservation.payment_intent_id,
                to_cents(reservation.refund.monto),
                f"refund-{refund_id}",
                metadata={"reembolso_id": refund_id},
            )
        except ProviderRejected as error:
            self._store.mark_failed(refund_id)
            raise RefundProviderError("The payment provider rejected the refund") from error
        except ProviderUnavailable as error:
            # Unknown outcome: the request may have reached Stripe. The row stays PENDIENTE and
            # nothing retries it; only the refund.updated webhook or an operator settles it.
            raise RefundProviderError("The payment provider is unavailable") from error

        estado = (
            REEMBOLSO_FALLIDO
            if provider.status in _FAILED_PROVIDER_STATUSES
            else REEMBOLSO_PENDIENTE
        )
        self._store.record_provider_answer(refund_id, provider.id, estado)
        return RefundOutcome(
            record=replace(reservation.refund, estado=estado),
            replayed=False,
        )

    def request_after_cancellation(self, actor: AccessProfile, cita_id: str) -> None:
        """Request the refund of a cancelled appointment, if its payment qualifies.

        The patient who owns the payment is the owner of the refund in every case:
        - the patient cancelled: CANCELACION_PACIENTE, requested as the actor (unchanged);
        - the doctor cancelled: CANCELACION_MEDICO, requested as the patient, because the
          doctor is not the owner of the payment and must not be recorded as such.

        Runs after the cancellation is committed and never raises: a refused or failed refund
        must not turn a cancellation into an error, and nothing about it reaches the answer.
        The reason is logged by code only.
        """
        try:
            found = self._store.find_confirmed_payment_for_appointment(cita_id)
            if found is None:
                return
            pago_id, patient_user_id = found
            if str(actor.user_id).lower() == patient_user_id:
                acting, motivo = actor, "CANCELACION_PACIENTE"
            else:
                # System request on behalf of the patient: the refund is for the payment owner,
                # and the only capability granted is requesting it.
                acting = AccessProfile(
                    user_id=patient_user_id,
                    status=actor.status,
                    roles=frozenset(),
                    permissions=frozenset({PERM_CHECKOUT}),
                )
                motivo = "CANCELACION_MEDICO"
            self.request_refund(
                acting,
                pago_id=pago_id,
                motivo_codigo=motivo,
                idempotency_key=f"auto-cancel-{cita_id}",
            )
        except RefundDenied as refused:
            logger.info("automatic refund not requested: %s", refused.detail)
        except (RefundProviderError, AppError) as failure:
            logger.warning("automatic refund failed: %s", type(failure).__name__)
        except Exception as unexpected:  # the cancellation has already committed
            logger.error("automatic refund error: %s", type(unexpected).__name__)

__all__ = [
    "RefundDenied",
    "RefundOutcome",
    "RefundProviderError",
    "RefundRecord",
    "RefundService",
    "refund_request_id",
]

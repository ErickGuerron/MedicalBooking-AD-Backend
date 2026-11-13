"""SQL Server adapter for refund requests.

The payment row is locked (UPDLOCK, ROWLOCK) for the whole check-and-insert, so two requests for
the same payment cannot both pass the eligibility rules. Parameterized SQL only. The provider is
never called from here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, text

from medicalbooking.modules.payments.application.refund_service import (
    RefundRecord,
    RefundReservation,
)
from medicalbooking.modules.payments.domain.refunds import (
    REFUND_WINDOW_DAYS,
    RefundCase,
)
from medicalbooking.modules.payments.infrastructure.billing_repositories import (
    emit_reembolso_snapshot,
)

_LOCK_PAYMENT = text(
    "SELECT p.id, p.estado, p.monto, p.pagado_en, p.creado_en, p.stripe_payment_intent_id, "
    "pa.id AS paciente_id, c.estado AS cita_estado, c.cancelada_en, h.inicio_utc, "
    "cm.inicio_atencion "
    "FROM pagos AS p WITH (UPDLOCK, ROWLOCK) "
    "JOIN facturas AS f ON f.id = p.factura_id "
    "JOIN pacientes AS pa ON pa.id = f.paciente_id "
    "LEFT JOIN citas AS c ON c.id = f.cita_id "
    "LEFT JOIN horarios_disponibles AS h ON h.id = c.horario_id "
    "LEFT JOIN consultas_medicas AS cm ON cm.cita_id = c.id "
    "WHERE p.id = :pago AND pa.usuario_id = :usuario"
)
_STORED_REFUND = text(
    "SELECT id, pago_id, estado, monto, motivo, solicitado_en FROM reembolsos WHERE id = :id"
)
_ACTIVE_STATES = text("SELECT estado FROM reembolsos WHERE pago_id = :pago")
_RECENT_REQUESTS = text(
    "SELECT COUNT(*) FROM reembolsos AS r "
    "JOIN pagos AS p2 ON p2.id = r.pago_id "
    "JOIN facturas AS f2 ON f2.id = p2.factura_id "
    "WHERE f2.paciente_id = :paciente AND r.solicitado_en >= :desde"
)
_INSERT_REFUND = text(
    "INSERT INTO reembolsos (id, pago_id, monto, estado, motivo, solicitado_en) "
    "VALUES (:id, :pago, :monto, 'PENDIENTE', :motivo, :ahora)"
)
_MARK_FAILED = text(
    "UPDATE reembolsos SET estado = 'FALLIDO' "
    "WHERE id = :id AND estado = 'PENDIENTE' AND stripe_refund_id IS NULL"
)
_RECORD_ANSWER = text(
    "UPDATE reembolsos SET stripe_refund_id = :sid, estado = :estado "
    "WHERE id = :id AND estado = 'PENDIENTE'"
)
_NO_CITA_START = datetime(1, 1, 1, tzinfo=UTC)


class SqlRefundStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

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
        with self._engine.begin() as conn:
            pago = conn.execute(_LOCK_PAYMENT, {"pago": pago_id, "usuario": owner_user_id}).first()
            if pago is None:
                return None
            stored = conn.execute(_STORED_REFUND, {"id": refund_id}).first()
            if stored is not None:
                # A replay of the same key: the stored refund, no eligibility check, no insert.
                return RefundReservation(
                    refund=_record(stored, motivo_codigo=stored.motivo),
                    created=False,
                    payment_intent_id=None,
                )
            activos = tuple(row.estado for row in conn.execute(_ACTIVE_STATES, {"pago": pago_id}))
            desde = _utc_naive(now - timedelta(days=REFUND_WINDOW_DAYS))
            recientes = conn.execute(
                _RECENT_REQUESTS, {"paciente": pago.paciente_id, "desde": desde}
            ).scalar_one()
            monto = Decimal(pago.monto)
            check(
                RefundCase(
                    pago_estado=pago.estado,
                    monto_pagado=monto,
                    pagado_en=_aware(pago.pagado_en or pago.creado_en),
                    cita_estado=pago.cita_estado or "",
                    cancelada_en=_aware(pago.cancelada_en),
                    cita_inicio=_aware(pago.inicio_utc) or _NO_CITA_START,
                    consulta_atendida=pago.inicio_atencion is not None,
                    reembolsos_activos=activos,
                    solicitudes_recientes=int(recientes),
                    motivo_codigo=motivo_codigo,
                    # The amount is never taken from the client: the full payment is requested.
                    monto_solicitado=monto,
                )
            )
            conn.execute(
                _INSERT_REFUND,
                {
                    "id": refund_id,
                    "pago": pago_id,
                    "monto": monto,
                    # reembolsos.motivo holds the closed reason code only; the free text is
                    # validated by the request but has no column yet.
                    "motivo": motivo_codigo,
                    "ahora": _utc_naive(now),
                },
            )
            emit_reembolso_snapshot(conn, refund_id, _utc_naive(now))
        return RefundReservation(
            refund=RefundRecord(
                id=refund_id,
                pago_id=pago_id,
                estado="PENDIENTE",
                monto=monto,
                motivo_codigo=motivo_codigo,
                solicitado_en=now,
            ),
            created=True,
            payment_intent_id=pago.stripe_payment_intent_id,
        )

    def find_confirmed_payment_for_appointment(self, cita_id: str) -> tuple[str, str] | None:
        """The newest confirmed payment of an appointment and the patient who owns it, if any.

        The owner is read from the invoice, never from the caller: a doctor's cancellation
        still refunds the patient who paid.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT TOP (1) p.id, pa.usuario_id AS patient_user_id FROM pagos AS p "
                    "JOIN facturas AS f ON f.id = p.factura_id "
                    "JOIN pacientes AS pa ON pa.id = f.paciente_id "
                    "WHERE f.cita_id = :cita AND p.estado = 'CONFIRMADO' "
                    "ORDER BY p.creado_en DESC"
                ),
                {"cita": cita_id},
            ).first()
        if row is None:
            return None
        return str(row.id).lower(), str(row.patient_user_id).lower()

    def mark_failed(self, refund_id: str) -> None:
        with self._engine.begin() as conn:
            changed = conn.execute(_MARK_FAILED, {"id": refund_id}).rowcount == 1
            if changed:
                emit_reembolso_snapshot(conn, refund_id, _utc_naive(datetime.now(UTC)))

    def record_provider_answer(self, refund_id: str, stripe_refund_id: str, estado: str) -> None:
        with self._engine.begin() as conn:
            changed = (
                conn.execute(
                    _RECORD_ANSWER, {"id": refund_id, "sid": stripe_refund_id, "estado": estado}
                ).rowcount
                == 1
            )
            if changed:
                emit_reembolso_snapshot(conn, refund_id, _utc_naive(datetime.now(UTC)))


def _record(row, *, motivo_codigo: str) -> RefundRecord:
    return RefundRecord(
        id=str(row.id).lower(),
        pago_id=str(row.pago_id).lower(),
        estado=row.estado,
        monto=Decimal(row.monto),
        motivo_codigo=motivo_codigo,
        solicitado_en=_aware(row.solicitado_en),
    )


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)

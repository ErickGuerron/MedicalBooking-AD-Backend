"""Clinical proposals (E5): deterministic evaluation, reading and one immutable human review.

A proposal is preliminary. It never states a diagnosis, and the original proposal and its
findings are never rewritten: a review adds a row to the decision history.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from medicalbooking.modules.clinical.application.ports import ClinicalStore, ProposalView
from medicalbooking.modules.clinical.domain.evaluation import MOTOR_VERSION, evaluate
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.domain.errors import Conflict, Forbidden, NotFound, ValidationFailed

PERM_READ = "resultados.leer_con_acceso"
PERM_REVIEW = "revisiones.registrar"
DECISIONS = {"ACEPTAR": "ACEPTADA", "CORREGIR": "CORREGIDA", "RECHAZAR": "RECHAZADA"}
_FORBIDDEN = "Access to this resource is not allowed"


class ProposalService:
    def __init__(self, store: ClinicalStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def evaluate(self, actor: AccessProfile, order_id: str) -> ProposalView:
        self._deny_without(actor, PERM_READ, "propuesta_clinica", order_id, "evaluar")
        order = self._store.load_order_for_evaluation(order_id)
        if order is None or order.medico_user_id != actor.user_id:
            self._audit(
                actor,
                order.paciente_id if order else None,
                "orden_laboratorio",
                order_id,
                "evaluar",
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        if order.estado != "COMPLETADA":
            raise Conflict("The order results must be complete before evaluation")

        now = self._clock()
        evaluation = evaluate(list(order.results), self._store.validated_rules(now))
        status, _proposal_id = self._store.record_proposal(order_id, evaluation, MOTOR_VERSION, now)
        if status == "NOT_COMPLETED":
            raise Conflict("The order results must be complete before evaluation")
        if status == "ALREADY_EXISTS":
            raise Conflict("This order already has a proposal")
        self._audit(actor, order.paciente_id, "propuesta_clinica", order_id, "evaluar")
        return self._store.get_proposal(order_id)

    def get_proposal(self, actor: AccessProfile, order_id: str) -> ProposalView:
        self._deny_without(actor, PERM_READ, "propuesta_clinica", order_id, "consultar")
        order = self._store.load_order_for_evaluation(order_id)
        if order is None or order.medico_user_id != actor.user_id:
            self._audit(
                actor,
                order.paciente_id if order else None,
                "propuesta_clinica",
                order_id,
                "consultar",
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        proposal = self._store.get_proposal(order_id)
        if proposal is None:
            raise NotFound("No proposal for this order yet")
        self._audit(actor, order.paciente_id, "propuesta_clinica", order_id, "consultar")
        return proposal

    def review(
        self,
        actor: AccessProfile,
        proposal_id: str,
        *,
        decision: str,
        motivo: str | None,
        conclusion_corregida: str | None,
    ) -> None:
        self._deny_without(actor, PERM_REVIEW, "propuesta_clinica", proposal_id, "revisar")
        owner = self._store.proposal_owner(proposal_id)
        if owner is None or owner.medico_user_id != actor.user_id:
            self._audit(
                actor,
                owner.paciente_id if owner else None,
                "propuesta_clinica",
                proposal_id,
                "revisar",
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        if decision not in DECISIONS:
            raise ValidationFailed("Decision must be ACEPTAR, CORREGIR or RECHAZAR")
        if decision == "CORREGIR" and not (conclusion_corregida or "").strip():
            raise ValidationFailed("A correction needs the corrected conclusion")
        if decision == "RECHAZAR" and not (motivo or "").strip():
            raise ValidationFailed("A rejection needs a reason")
        if motivo is not None and len(motivo) > 2000:
            raise ValidationFailed("Reason is too long")

        status = self._store.review_proposal(
            proposal_id,
            actor.user_id,
            decision,
            motivo,
            conclusion_corregida,
            self._clock(),
        )
        if status == "NOT_FOUND":
            raise NotFound("Proposal not found")
        if status == "NOT_PENDING":
            raise Conflict("This proposal was already decided or has no findings to review")
        self._audit(actor, owner.paciente_id, "propuesta_clinica", proposal_id, "revisar")

    # ---------- helpers ----------

    def _deny_without(
        self,
        actor: AccessProfile,
        permission: str,
        entidad: str,
        entidad_id: str,
        accion: str,
    ) -> None:
        """A missing permission is a denial too: it is audited before it is refused (S12)."""
        if permission not in actor.permissions:
            self._audit(actor, None, entidad, entidad_id, accion, "DENEGADO")
            raise Forbidden(_FORBIDDEN)

    def _audit(
        self,
        actor: AccessProfile,
        paciente_id: str | None,
        entidad: str,
        entidad_id: str,
        accion: str,
        resultado: str = "OK",
    ) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=paciente_id,
                entidad=entidad,
                entidad_id=entidad_id,
                accion=accion,
                resultado=resultado,
                occurred_at=self._clock(),
            )
        )

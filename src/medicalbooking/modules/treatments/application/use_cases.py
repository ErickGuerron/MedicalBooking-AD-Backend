"""Treatment use cases (S4-S6, S10): a structured treatment per appointment and its follow-ups.

Authorization is per resource. A missing resource and a foreign one answer the same 403, so
identifiers do not leak. Only the doctor of the appointment writes; the owning patient reads.
Every read and write is audited without clinical values.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.modules.laboratory.domain.policies import (
    PERM_RESULT_READ_CONTEXT,
    PERM_RESULT_READ_OWN,
    PERM_REVIEW,
)
from medicalbooking.modules.treatments.application.ports import (
    FollowUp,
    NewFollowUp,
    NewTreatment,
    Treatment,
    TreatmentStore,
    TreatmentUpdate,
)
from medicalbooking.modules.treatments.domain.policies import (
    ACTIVO,
    validate_descripcion,
    validate_estado,
    validate_fechas,
    validate_nota,
    validate_transition,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed

_FORBIDDEN = "Access to this resource is not allowed"
_TREATMENT = "tratamiento"
_FOLLOW_UP = "seguimiento_tratamiento"


class TreatmentService:
    def __init__(
        self,
        store: TreatmentStore,
        clock: Callable[[], datetime],
        correlation_id: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._store = store
        self._clock = clock
        # Supplied by the composition root: the current request's correlation id.
        self._correlation_id = correlation_id

    # ---------- treatments ----------

    def create_treatment(
        self,
        actor: AccessProfile,
        cita_id: str,
        *,
        descripcion: str,
        fecha_inicio: date,
        estado: str = ACTIVO,
        fecha_fin: date | None = None,
    ) -> Treatment:
        _require(actor, PERM_REVIEW)
        cita = self._store.get_cita(str(cita_id))
        if cita is None or not self._is_treating_doctor(actor, cita.medico_id):
            raise self._denied(
                actor, cita.paciente_id if cita else None, "cita", str(cita_id), "crear"
            )
        validate_descripcion(descripcion)
        validate_estado(estado)
        validate_fechas(fecha_inicio, fecha_fin, estado)

        treatment = self._store.create_treatment(
            NewTreatment(
                treatment_id=str(uuid.uuid4()),
                cita_id=cita.cita_id,
                descripcion=descripcion,
                estado=estado,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                creado_en=self._clock(),
            )
        )
        self._audit(actor, treatment.paciente_id, _TREATMENT, treatment.id, "crear")
        return treatment

    def list_treatments(self, actor: AccessProfile, cita_id: str) -> list[Treatment]:
        """Treatments of one appointment, for its treating doctor or its owning patient."""
        cita = self._store.get_cita(str(cita_id))
        if cita is None or not self._can_read_cita(actor, cita.paciente_id, cita.medico_id):
            raise self._denied(
                actor,
                cita.paciente_id if cita else None,
                _TREATMENT,
                str(cita_id),
                "consultar",
            )
        self._audit(actor, cita.paciente_id, _TREATMENT, cita.cita_id, "consultar")
        return self._store.list_treatments(cita.cita_id)

    def update_treatment(
        self,
        actor: AccessProfile,
        treatment_id: str,
        *,
        descripcion: str | None = None,
        estado: str | None = None,
        fecha_fin: date | None = None,
    ) -> Treatment:
        _require(actor, PERM_REVIEW)
        current = self._store.get_treatment(str(treatment_id))
        if current is None or not self._is_treating_doctor(actor, current.medico_id):
            raise self._denied(
                actor,
                current.paciente_id if current else None,
                _TREATMENT,
                str(treatment_id),
                "actualizar",
            )
        if descripcion is None and estado is None and fecha_fin is None:
            raise ValidationFailed("Nothing to update")

        target_descripcion = current.descripcion if descripcion is None else descripcion
        target_estado = current.estado if estado is None else estado
        target_fin = current.fecha_fin if fecha_fin is None else fecha_fin
        if descripcion is not None:
            validate_descripcion(descripcion)
        if estado is not None:
            validate_estado(estado)
        validate_transition(current.estado, target_estado)
        validate_fechas(current.fecha_inicio, target_fin, target_estado)

        updated = self._store.update_treatment(
            TreatmentUpdate(
                treatment_id=current.id,
                expected_estado=current.estado,
                descripcion=target_descripcion,
                estado=target_estado,
                fecha_fin=target_fin,
                actualizado_en=self._clock(),
            )
        )
        if updated is None:
            raise Conflict("The treatment changed; reload it and retry")
        self._audit(actor, current.paciente_id, _TREATMENT, current.id, "actualizar")
        return updated

    # ---------- follow-ups (append-only) ----------

    def add_followup(self, actor: AccessProfile, treatment_id: str, nota: str) -> FollowUp:
        _require(actor, PERM_REVIEW)
        treatment = self._store.get_treatment(str(treatment_id))
        if treatment is None or not self._is_treating_doctor(actor, treatment.medico_id):
            raise self._denied(
                actor,
                treatment.paciente_id if treatment else None,
                _FOLLOW_UP,
                str(treatment_id),
                "registrar",
            )
        validate_nota(nota)

        followup = self._store.append_followup(
            NewFollowUp(
                followup_id=str(uuid.uuid4()),
                tratamiento_id=treatment.id,
                nota=nota,
                registrado_por_user_id=actor.user_id,
                registrado_en=self._clock(),
            )
        )
        self._audit(actor, treatment.paciente_id, _FOLLOW_UP, followup.id, "registrar")
        return followup

    def list_followups(self, actor: AccessProfile, treatment_id: str) -> list[FollowUp]:
        treatment = self._store.get_treatment(str(treatment_id))
        if treatment is None or not self._can_read_cita(
            actor, treatment.paciente_id, treatment.medico_id
        ):
            raise self._denied(
                actor,
                treatment.paciente_id if treatment else None,
                _FOLLOW_UP,
                str(treatment_id),
                "consultar",
            )
        self._audit(actor, treatment.paciente_id, _FOLLOW_UP, treatment.id, "consultar")
        return self._store.list_followups(treatment.id)

    # ---------- helpers ----------

    def _is_treating_doctor(self, actor: AccessProfile, medico_id: str) -> bool:
        return (
            PERM_REVIEW in actor.permissions
            and self._store.resolve_medico_id(actor.user_id) == medico_id
        )

    def _can_read_cita(self, actor: AccessProfile, paciente_id: str, medico_id: str) -> bool:
        if PERM_RESULT_READ_OWN in actor.permissions:
            if self._store.resolve_patient_id(actor.user_id) == paciente_id:
                return True
        return (
            PERM_RESULT_READ_CONTEXT in actor.permissions
            and self._store.resolve_medico_id(actor.user_id) == medico_id
        )

    def _denied(
        self,
        actor: AccessProfile,
        paciente_id: str | None,
        entidad: str,
        entidad_id: str,
        accion: str,
    ) -> Forbidden:
        # Denied access is audited too; the resource may not exist, so the patient may be unknown.
        # Callers raise the returned error so the control flow is explicit to readers and tools.
        self._audit(actor, paciente_id, entidad, entidad_id, accion, "DENEGADO")
        return Forbidden(_FORBIDDEN)

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
                correlation_id=self._correlation_id(),
            )
        )


def _require(actor: AccessProfile, permission: str) -> None:
    if permission not in actor.permissions:
        raise Forbidden(_FORBIDDEN)

"""Patient clinical profile for the WHO 2024 adjustments (feature doc S12).

The patient declares their own altitude of residence and smoking status. Only that patient can
write it; a denial is audited before it is refused. The values are self-reported and may be unknown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from medicalbooking.modules.clinical.application.ports import ClinicalStore
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.domain.errors import Forbidden, NotFound, ValidationFailed

PERM_EDIT = "pacientes.editar_propio"
_FORBIDDEN = "Access to this resource is not allowed"
_ENTIDAD = "perfil_clinico"
MAX_ALTITUDE_M = 8848
MAX_CIGARETTES = 200


@dataclass(frozen=True)
class ClinicalProfile:
    altitud_residencia_m: int | None
    fumador: bool | None
    cigarrillos_dia: int | None


class PatientProfileService:
    def __init__(self, store: ClinicalStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def save(self, actor: AccessProfile, profile: ClinicalProfile) -> None:
        if PERM_EDIT not in actor.permissions:
            self._audit(actor, None, "DENEGADO")
            raise Forbidden(_FORBIDDEN)
        _validate(profile)
        paciente_id = self._store.save_patient_clinical_profile(
            actor.user_id,
            profile.altitud_residencia_m,
            profile.fumador,
            profile.cigarrillos_dia,
        )
        if paciente_id is None:
            raise NotFound("No patient profile for this user")
        self._audit(actor, paciente_id, "OK")

    def _audit(self, actor: AccessProfile, paciente_id: str | None, resultado: str) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=paciente_id,
                entidad=_ENTIDAD,
                entidad_id=actor.user_id,
                accion="actualizar",
                resultado=resultado,
                occurred_at=self._clock(),
            )
        )


def _validate(profile: ClinicalProfile) -> None:
    # Altitude and smoking are mandatory (S19): the WHO 2024 adjustment needs both.
    altitude = profile.altitud_residencia_m
    if altitude is None or not 0 <= altitude <= MAX_ALTITUDE_M:
        raise ValidationFailed("Altitude of residence is required, between 0 and 8848 metres")
    if profile.fumador is None:
        raise ValidationFailed("Smoking status is required")
    cigarettes = profile.cigarrillos_dia
    if profile.fumador is True and cigarettes is None:
        raise ValidationFailed("A smoker must declare cigarettes per day")
    if cigarettes is not None:
        if not 0 <= cigarettes <= MAX_CIGARETTES:
            raise ValidationFailed("Cigarettes per day must be between 0 and 200")
        if profile.fumador is not True:
            raise ValidationFailed("A cigarette count needs a declared smoker")

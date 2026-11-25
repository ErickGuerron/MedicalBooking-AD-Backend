"""Clinical history use cases.

Access is decided in this order, every time: permission, then the relation with the patient (the
treating doctor has an appointment with them; the patient is the owner), then the resource itself.
A missing resource and a foreign one answer the same 403, so identifiers do not leak. Every denial,
read and write is audited without clinical values; the clinical change itself is kept in the
append-only historia_eventos table.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from medicalbooking.modules.history.application.ports import Entry, HistoryStore, VitalSigns
from medicalbooking.modules.history.domain.policies import (
    ALERGIA,
    ANTECEDENTE,
    MEDICACION,
    PERM_DECLARAR,
    PERM_LEER,
    PERM_LEER_PROPIA,
    PERM_REGISTRAR,
    PROBLEMA,
    TABLE_BY_KIND,
    VACUNA,
    can_verify,
    origin_and_verification,
    validate_alergia,
    validate_antecedente,
    validate_medicacion,
    validate_motivo,
    validate_problema,
    validate_transition,
    validate_vacuna,
    validate_vitales,
)
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed

_FORBIDDEN = "Access to this resource is not allowed"
_HISTORY = "historia"
_VITALS = "signos_vitales"
_VITAL_KEYS = (
    "peso_kg",
    "talla_cm",
    "pa_sistolica",
    "pa_diastolica",
    "frecuencia_cardiaca",
    "temperatura_c",
    "saturacion",
)
_KINDS = {
    "alergia": ALERGIA,
    "antecedente": ANTECEDENTE,
    "medicacion": MEDICACION,
    "problema": PROBLEMA,
    "vacuna": VACUNA,
}
# Kinds a patient may declare about themselves. Problems are a doctor's judgement.
_PATIENT_DECLARABLE = frozenset({"alergia", "antecedente", "medicacion", "vacuna"})


@dataclass(frozen=True)
class HistoryView:
    """The history of one patient, grouped by kind. Active allergies come first."""

    paciente_id: str
    entries: dict[str, list[Entry]] = field(default_factory=dict)
    vitals: list[VitalSigns] = field(default_factory=list)


class HistoryService:
    def __init__(
        self,
        store: HistoryStore,
        clock: Callable[[], datetime],
        correlation_id: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._correlation_id = correlation_id

    # ---------- reading ----------

    def read_history(self, actor: AccessProfile, paciente_id: str) -> HistoryView:
        if not self._can_read(actor, paciente_id):
            raise self._denied(actor, paciente_id, _HISTORY, paciente_id, "consultar")
        entries: dict[str, list[Entry]] = {}
        for kind in TABLE_BY_KIND:
            rows = self._store.list_entries(kind, paciente_id)
            # Active first, then the most recent. Erroneous entries stay listed, last.
            initial = _KINDS[kind].initial
            entries[kind] = sorted(
                rows, key=lambda e: (e.estado != initial, -e.registrada_en.timestamp())
            )
        vitals = self._store.list_vitals(paciente_id)
        self._audit(actor, paciente_id, _HISTORY, paciente_id, "consultar", "OK")
        return HistoryView(paciente_id=paciente_id, entries=entries, vitals=vitals)

    # ---------- entries ----------

    def create_entry(
        self, actor: AccessProfile, kind: str, paciente_id: str, body: dict[str, Any]
    ) -> Entry:
        if kind not in TABLE_BY_KIND:
            raise ValidationFailed("Unknown kind of history entry")
        is_doctor = self._is_treating(actor, paciente_id)
        is_own = (
            not is_doctor
            and PERM_DECLARAR in actor.permissions
            and kind in _PATIENT_DECLARABLE
            and self._store.resolve_patient_id(actor.user_id) == paciente_id
        )
        if not (is_doctor or is_own):
            raise self._denied(actor, paciente_id, kind, "", "crear")
        fields = _validated(kind, body, self._clock().date())
        origen, verificada = origin_and_verification(is_doctor=is_doctor)
        now = self._clock()
        entry_id = str(uuid.uuid4())
        self._store.insert_entry(
            kind,
            entry_id,
            paciente_id,
            fields,
            origen=origen,
            verificada=verificada,
            verificada_por=actor.user_id if verificada else None,
            registrada_por=actor.user_id,
            now=now,
        )
        created = self._store.get_entry(kind, entry_id)
        if created is None:  # pragma: no cover - the insert just succeeded
            raise Conflict("The entry could not be read back")
        self._event(actor, paciente_id, kind, entry_id, "CREAR", None, None, _state(created), now)
        self._audit(actor, paciente_id, kind, entry_id, "crear", "OK")
        return created

    def verify_entry(
        self, actor: AccessProfile, kind: str, entry_id: str, *, paciente_id: str
    ) -> Entry:
        entry = self._load_for_patient(kind, entry_id, paciente_id)
        if entry is None or not self._is_treating(actor, entry.paciente_id):
            raise self._denied(
                actor, entry.paciente_id if entry else None, kind, entry_id, "verificar"
            )
        can_verify(entry.origen, entry.verificada)
        now = self._clock()
        if not self._store.verify_entry(kind, entry_id, actor.user_id, now):
            raise Conflict("This entry is already verified")
        self._event(
            actor,
            entry.paciente_id,
            kind,
            entry_id,
            "VERIFICAR",
            None,
            _state(entry),
            _state(self._store.get_entry(kind, entry_id)),
            now,
        )
        self._audit(actor, entry.paciente_id, kind, entry_id, "verificar", "OK")
        return self._store.get_entry(kind, entry_id)  # type: ignore[return-value]

    def change_estado(
        self,
        actor: AccessProfile,
        kind: str,
        entry_id: str,
        *,
        paciente_id: str,
        expected: str,
        target: str,
        motivo: str | None,
    ) -> Entry:
        entry = self._load_for_patient(kind, entry_id, paciente_id)
        if entry is None or not self._is_treating(actor, entry.paciente_id):
            raise self._denied(
                actor, entry.paciente_id if entry else None, kind, entry_id, "actualizar"
            )
        if entry.estado != expected:
            # Someone else changed it after this doctor read it: refuse, do not overwrite.
            raise Conflict("The entry changed since it was read")
        validate_transition(_KINDS[kind], entry.estado or "", target, motivo)
        now = self._clock()
        if not self._store.change_estado(kind, entry_id, expected, target, now):
            raise Conflict("The entry changed since it was read")
        after = self._store.get_entry(kind, entry_id)
        self._event(
            actor,
            entry.paciente_id,
            kind,
            entry_id,
            "ESTADO",
            motivo,
            _state(entry),
            _state(after),
            now,
        )
        self._audit(actor, entry.paciente_id, kind, entry_id, "actualizar", "OK")
        return after  # type: ignore[return-value]

    # ---------- vital signs ----------

    def record_vitals(
        self, actor: AccessProfile, cita_id: str, values: dict[str, Any]
    ) -> VitalSigns:
        cita = self._store.get_cita(cita_id)
        treating = (
            cita is not None
            and PERM_REGISTRAR in actor.permissions
            and self._store.resolve_medico_id(actor.user_id) == cita.medico_id
        )
        if not treating:
            raise self._denied(actor, cita.paciente_id if cita else None, _VITALS, cita_id, "crear")
        measures = validate_vitales({k: values.get(k) for k in _VITAL_KEYS})
        now = self._clock()
        vitals_id = str(uuid.uuid4())
        self._store.insert_vitals(
            vitals_id,
            cita.paciente_id,
            cita_id,
            measures,
            registrada_por=actor.user_id,
            now=now,
        )
        self._event(
            actor,
            cita.paciente_id,
            _VITALS,
            vitals_id,
            "CREAR",
            None,
            None,
            json.dumps(measures, default=str),
            now,
        )
        self._audit(actor, cita.paciente_id, _VITALS, vitals_id, "crear", "OK")
        return VitalSigns(
            id=vitals_id,
            paciente_id=cita.paciente_id,
            cita_id=cita_id,
            corrige_a=None,
            motivo_correccion=None,
            registrada_por=actor.user_id,
            registrada_en=now,
            values=measures,
        )

    def correct_vitals(
        self, actor: AccessProfile, vitals_id: str, values: dict[str, Any], motivo: str | None
    ) -> VitalSigns:
        """A correction is a new entry that points at the one it corrects."""
        original = self._store.get_vitals(vitals_id)
        cita = self._store.get_cita(original.cita_id) if original else None
        treating = (
            original is not None
            and cita is not None
            and PERM_REGISTRAR in actor.permissions
            and self._store.resolve_medico_id(actor.user_id) == cita.medico_id
        )
        if not treating or original is None:
            raise self._denied(
                actor, original.paciente_id if original else None, _VITALS, vitals_id, "corregir"
            )
        text = validate_motivo(motivo)
        measures = validate_vitales({k: values.get(k) for k in _VITAL_KEYS})
        now = self._clock()
        new_id = str(uuid.uuid4())
        self._store.insert_vitals(
            new_id,
            original.paciente_id,
            original.cita_id,
            measures,
            registrada_por=actor.user_id,
            now=now,
            corrige_a=original.id,
            motivo_correccion=text,
        )
        self._event(
            actor,
            original.paciente_id,
            _VITALS,
            new_id,
            "CORREGIR",
            text,
            json.dumps(original.values, default=str, sort_keys=True),
            json.dumps(measures, default=str, sort_keys=True),
            now,
        )
        self._audit(actor, original.paciente_id, _VITALS, new_id, "corregir", "OK")
        return VitalSigns(
            id=new_id,
            paciente_id=original.paciente_id,
            cita_id=original.cita_id,
            corrige_a=original.id,
            motivo_correccion=text,
            registrada_por=actor.user_id,
            registrada_en=now,
            values=measures,
        )

    # ---------- helpers ----------

    def _load(self, kind: str, entry_id: str) -> Entry | None:
        if kind not in TABLE_BY_KIND:
            raise ValidationFailed("Unknown kind of history entry")
        return self._store.get_entry(kind, entry_id)

    def _load_for_patient(self, kind: str, entry_id: str, paciente_id: str) -> Entry | None:
        """An entry under the path's patient. Another patient's entry reads as missing."""
        entry = self._load(kind, entry_id)
        return entry if entry is not None and entry.paciente_id == paciente_id else None

    def _is_treating(self, actor: AccessProfile, paciente_id: str) -> bool:
        if PERM_REGISTRAR not in actor.permissions:
            return False
        medico_id = self._store.resolve_medico_id(actor.user_id)
        return medico_id is not None and self._store.doctor_treats_patient(medico_id, paciente_id)

    def _can_read(self, actor: AccessProfile, paciente_id: str) -> bool:
        if PERM_LEER in actor.permissions and self._is_treating_for_read(actor, paciente_id):
            return True
        return (
            PERM_LEER_PROPIA in actor.permissions
            and self._store.resolve_patient_id(actor.user_id) == paciente_id
        )

    def _is_treating_for_read(self, actor: AccessProfile, paciente_id: str) -> bool:
        medico_id = self._store.resolve_medico_id(actor.user_id)
        return medico_id is not None and self._store.doctor_treats_patient(medico_id, paciente_id)

    def _denied(
        self,
        actor: AccessProfile,
        paciente_id: str | None,
        entidad: str,
        entidad_id: str,
        accion: str,
    ) -> Forbidden:
        # Denied access is audited too. The caller raises the returned error explicitly.
        self._audit(actor, paciente_id, entidad, entidad_id, accion, "DENEGADO")
        return Forbidden(_FORBIDDEN)

    def _event(
        self,
        actor: AccessProfile,
        paciente_id: str,
        entidad: str,
        entidad_id: str,
        accion: str,
        motivo: str | None,
        antes: str | None,
        despues: str | None,
        now: datetime,
    ) -> None:
        self._store.append_event(
            paciente_id=paciente_id,
            entidad=entidad,
            entidad_id=entidad_id,
            accion=accion,
            actor_user_id=actor.user_id,
            motivo=motivo,
            antes=antes,
            despues=despues,
            now=now,
        )

    def _audit(
        self,
        actor: AccessProfile,
        paciente_id: str | None,
        entidad: str,
        entidad_id: str,
        accion: str,
        resultado: str,
    ) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=paciente_id,
                entidad=entidad,
                entidad_id=entidad_id or None,
                accion=accion,
                resultado=resultado,
                occurred_at=self._clock(),
                correlation_id=self._correlation_id(),
            )
        )


def _state(entry: Entry | None) -> str | None:
    """The values of an entry as JSON, for the event log. None when the entry is missing."""
    if entry is None:
        return None
    return json.dumps(
        {
            "estado": entry.estado,
            "origen": entry.origen,
            "verificada": entry.verificada,
            **entry.data,
        },
        default=str,
        sort_keys=True,
    )


def _validated(kind: str, body: dict[str, Any], today: date) -> dict[str, Any]:
    """Runs the kind's rules on the request body. Missing keys are None; unknown keys are ignored
    here because the route already accepts only the declared fields."""
    get = body.get
    if kind == "alergia":
        return validate_alergia(
            sustancia=get("sustancia"),
            categoria=get("categoria"),
            severidad=get("severidad"),
            reaccion=get("reaccion"),
        )
    if kind == "antecedente":
        return validate_antecedente(
            tipo=get("tipo"),
            descripcion=get("descripcion"),
            parentesco=get("parentesco"),
            cie11=get("cie11"),
        )
    if kind == "medicacion":
        return validate_medicacion(
            medicamento=get("medicamento"),
            dosis=get("dosis"),
            frecuencia=get("frecuencia"),
            inicio=get("inicio"),
        )
    if kind == "problema":
        return validate_problema(
            descripcion=get("descripcion"), cie11=get("cie11"), inicio=get("inicio")
        )
    return validate_vacuna(
        vacuna=get("vacuna"),
        dosis_numero=get("dosis_numero"),
        fecha=get("fecha"),
        lote=get("lote"),
        today=today,
    )

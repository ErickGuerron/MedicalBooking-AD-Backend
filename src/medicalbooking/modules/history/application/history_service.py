"""Clinical history use cases.

Access is decided in this order, every time: permission, then the relation with the patient (the
treating doctor has an appointment with them; the patient is the owner), then the resource itself.
A missing resource and a foreign one answer the same 403, so identifiers do not leak. Every denial,
read and write is audited without clinical values; the clinical change itself is kept in the
append-only historia_eventos table, which this module reads back page by page.

Entries that name a diagnosis, a vaccine or an allergen must use a code from a catalog. The name
written on the entry is a snapshot of the catalog at that moment.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from medicalbooking.modules.history.application.ports import (
    CatalogItem,
    Entry,
    EventRow,
    HistoryStore,
    VitalSigns,
)
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
    DateRange,
    can_verify,
    origin_and_verification,
    validate_alergia,
    validate_antecedente,
    validate_filtro_evento,
    validate_medicacion,
    validate_motivo,
    validate_pagina,
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

CATALOG_CIE11 = "cie11"
CATALOG_VACUNAS = "vacunas"
CATALOG_SUSTANCIAS = "sustancias_alergia"
CATALOGS = (CATALOG_CIE11, CATALOG_VACUNAS, CATALOG_SUSTANCIAS)
MAX_QUERY_CHARS = 100
MAX_CATALOG_RESULTS = 50


@dataclass(frozen=True)
class HistoryView:
    """The history of one patient, grouped by kind. Active allergies come first."""

    paciente_id: str
    entries: dict[str, list[Entry]] = field(default_factory=dict)
    vitals: list[VitalSigns] = field(default_factory=list)


@dataclass(frozen=True)
class EventPage:
    """One page of the event log. `incluye_actor` says whether the actor identity is in the rows."""

    items: list[EventRow]
    total: int
    limit: int
    offset: int
    incluye_actor: bool


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

    # ---------- the patient's own identity ----------

    def own_patient_id(self, actor: AccessProfile) -> str:
        """The patient behind the caller's account. Anyone else is refused the same way."""
        patient = (
            self._store.resolve_patient_id(actor.user_id)
            if PERM_LEER_PROPIA in actor.permissions
            else None
        )
        if patient is None:
            raise self._denied(actor, None, _HISTORY, "", "consultar")
        return patient

    # ---------- reading ----------

    def read_history(
        self, actor: AccessProfile, paciente_id: str, rango: DateRange | None = None
    ) -> HistoryView:
        if not self._can_read(actor, paciente_id):
            raise self._denied(actor, paciente_id, _HISTORY, paciente_id, "consultar")
        rango = rango or DateRange(desde=None, hasta=None)
        start, end = _bounds(rango)
        entries: dict[str, list[Entry]] = {}
        for kind, definition in _KINDS.items():
            rows = self._store.list_entries(kind, paciente_id, start, end)
            # Active first, then the most recent. Erroneous entries stay listed, last.
            entries[kind] = sorted(
                rows,
                key=lambda e, d=definition: (e.estado != d.initial, -e.registrada_en.timestamp()),
            )
        vitals = self._store.list_vitals(paciente_id, start, end)
        self._audit(actor, paciente_id, _HISTORY, paciente_id, "consultar", "OK")
        return HistoryView(paciente_id=paciente_id, entries=entries, vitals=vitals)

    def list_events(
        self,
        actor: AccessProfile,
        paciente_id: str,
        *,
        entidad: str | None = None,
        accion: str | None = None,
        rango: DateRange | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> EventPage:
        """The event log of one history, newest first. The treating doctor sees who acted;
        the patient sees what changed and when, but not which professional made it."""
        if not self._can_read(actor, paciente_id):
            raise self._denied(actor, paciente_id, _HISTORY, paciente_id, "consultar_eventos")
        entidad, accion = validate_filtro_evento(entidad, accion)
        size, skip = validate_pagina(limit, offset)
        rango = rango or DateRange(desde=None, hasta=None)
        start, end = _bounds(rango)
        is_doctor = self._is_treating(actor, paciente_id)
        rows, total = self._store.list_events(
            paciente_id,
            entidad=entidad,
            accion=accion,
            start=start,
            end=end,
            limit=size,
            offset=skip,
        )
        if not is_doctor:
            rows = [replace(row, actor_usuario_id=None) for row in rows]
        self._audit(actor, paciente_id, _HISTORY, paciente_id, "consultar_eventos", "OK")
        return EventPage(items=rows, total=total, limit=size, offset=skip, incluye_actor=is_doctor)

    # ---------- catalogs ----------

    def search_catalog(
        self, actor: AccessProfile, catalog: str, query: str | None, limit: int | None
    ) -> list[CatalogItem]:
        """Active catalog items for a picker. Any signed-in person may read the catalogs."""
        if catalog not in CATALOGS:
            raise ValidationFailed("'catalog' is not a known catalog")
        text = (query or "").strip()
        if len(text) > MAX_QUERY_CHARS:
            raise ValidationFailed(f"'q' must be at most {MAX_QUERY_CHARS} characters")
        size = 20 if limit is None else limit
        if isinstance(size, bool) or not 1 <= size <= MAX_CATALOG_RESULTS:
            raise ValidationFailed(f"'limit' must be from 1 to {MAX_CATALOG_RESULTS}")
        return self._store.search_catalog(catalog, text or None, size)

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
        fields = self._validated(kind, body, self._clock().date())
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
        after = self._store.get_entry(kind, entry_id)
        self._event(
            actor,
            entry.paciente_id,
            kind,
            entry_id,
            "VERIFICAR",
            None,
            _state(entry),
            _state(after),
            now,
        )
        self._audit(actor, entry.paciente_id, kind, entry_id, "verificar", "OK")
        return after  # type: ignore[return-value]

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

    # ---------- validation against the catalogs ----------

    def _validated(self, kind: str, body: dict[str, Any], today: date) -> dict[str, Any]:
        """The kind's rules on the request body, with catalog codes resolved to their names."""
        get = body.get
        if kind == "alergia":
            allergen = self._catalog(
                CATALOG_SUSTANCIAS, get("sustancia_codigo"), "sustancia_codigo"
            )
            fields = validate_alergia(
                sustancia=allergen.nombre,
                categoria=allergen.categoria or "OTRA",
                severidad=get("severidad"),
                reaccion=get("reaccion"),
            )
            return {**fields, "sustancia_codigo": allergen.codigo}
        if kind == "antecedente":
            fields = validate_antecedente(
                tipo=get("tipo"),
                descripcion=get("descripcion"),
                parentesco=get("parentesco"),
                cie11=get("cie11"),
            )
            self._require_cie11(fields["cie11"])
            return fields
        if kind == "medicacion":
            return validate_medicacion(
                medicamento=get("medicamento"),
                dosis=get("dosis"),
                frecuencia=get("frecuencia"),
                inicio=get("inicio"),
            )
        if kind == "problema":
            fields = validate_problema(
                descripcion=get("descripcion"), cie11=get("cie11"), inicio=get("inicio")
            )
            self._require_cie11(fields["cie11"])
            return fields
        vaccine = self._catalog(CATALOG_VACUNAS, get("vacuna_codigo"), "vacuna_codigo")
        fields = validate_vacuna(
            vacuna=vaccine.nombre,
            dosis_numero=get("dosis_numero"),
            fecha=get("fecha"),
            lote=get("lote"),
            today=today,
        )
        return {**fields, "vacuna_codigo": vaccine.codigo}

    def _catalog(self, catalog: str, codigo: Any, field_name: str) -> CatalogItem:
        if not isinstance(codigo, str) or not codigo.strip():
            raise ValidationFailed(f"'{field_name}' is required and must come from the catalog")
        item = self._store.catalog_item(catalog, codigo.strip())
        if item is None or not item.activo:
            raise ValidationFailed(f"'{field_name}' is not an active code of the catalog")
        return item

    def _require_cie11(self, code: str | None) -> None:
        """A CIE-11 code is optional; when present it must be an active code of the catalog."""
        if code is None:
            return
        item = self._store.catalog_item(CATALOG_CIE11, code)
        if item is None or not item.activo:
            raise ValidationFailed("'cie11' is not an active code of the CIE-11 catalog")

    # ---------- helpers ----------

    def _load_for_patient(self, kind: str, entry_id: str, paciente_id: str) -> Entry | None:
        """An entry under the path's patient. Another patient's entry reads as missing."""
        if kind not in TABLE_BY_KIND:
            raise ValidationFailed("Unknown kind of history entry")
        entry = self._store.get_entry(kind, entry_id)
        return entry if entry is not None and entry.paciente_id == paciente_id else None

    def _is_treating(self, actor: AccessProfile, paciente_id: str) -> bool:
        """Writing needs the registrar permission and an appointment with the patient."""
        if PERM_REGISTRAR not in actor.permissions:
            return False
        return self._has_relation(actor, paciente_id)

    def _can_read(self, actor: AccessProfile, paciente_id: str) -> bool:
        if PERM_LEER in actor.permissions and self._has_relation(actor, paciente_id):
            return True
        return (
            PERM_LEER_PROPIA in actor.permissions
            and self._store.resolve_patient_id(actor.user_id) == paciente_id
        )

    def _has_relation(self, actor: AccessProfile, paciente_id: str) -> bool:
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


def _bounds(rango: DateRange) -> tuple[datetime | None, datetime | None]:
    """Calendar days in UTC to a half-open [start, end) pair. An open side stays None."""
    start = datetime.combine(rango.desde, time.min, tzinfo=UTC) if rango.desde else None
    end = (
        datetime.combine(rango.hasta + timedelta(days=1), time.min, tzinfo=UTC)
        if rango.hasta
        else None
    )
    return start, end


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

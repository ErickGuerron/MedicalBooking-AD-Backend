"""Laboratory use cases (E3): orders, manual results, medical review, PDF documents.

Authorization is per resource. A missing resource and a foreign one answer the same
403, so identifiers do not leak. Every read and clinical write is audited without values.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from medicalbooking.modules.clinical.domain.references import PatientContext, classify
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import (
    AuditEvent,
    DocumentStorage,
    LabOrder,
    LabResult,
    LabStore,
    NewDocument,
    NewOrder,
    ResultEntry,
    StoredDocument,
)
from medicalbooking.modules.laboratory.domain.policies import (
    PERM_ORDER_CREATE,
    PERM_RESULT_READ_CONTEXT,
    PERM_RESULT_READ_OWN,
    PERM_RESULT_REGISTER,
    PERM_REVIEW,
    safe_filename,
    validate_conclusion,
    validate_document_type,
    validate_observations,
    validate_pdf,
    validate_priority,
    validate_result_values,
    validate_test_ids,
)
from medicalbooking.shared.domain.errors import (
    AppError,
    Conflict,
    Forbidden,
    NotFound,
    ValidationFailed,
)

_FORBIDDEN = "Access to this resource is not allowed"


class DocumentIntegrityError(AppError):
    status = 500
    title = "Document integrity check failed"


class LabService:
    def __init__(
        self,
        store: LabStore,
        storage: DocumentStorage,
        clock: Callable[[], datetime],
        correlation_id: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._clock = clock
        # Supplied by the composition root: the current request's correlation id.
        self._correlation_id = correlation_id

    # ---------- orders ----------

    def create_order(
        self,
        actor: AccessProfile,
        *,
        paciente_id: str,
        prioridad: str,
        observaciones: str | None,
        tipo_prueba_ids: list[str],
    ) -> LabOrder:
        _require(actor, PERM_ORDER_CREATE)
        medico_id = self._store.resolve_medico_id(actor.user_id)
        if medico_id is None:
            raise Forbidden(_FORBIDDEN)
        validate_priority(prioridad)
        validate_observations(observaciones)
        test_ids = validate_test_ids(list(tipo_prueba_ids))
        if not self._store.patient_exists(str(paciente_id)):
            raise NotFound("Patient not found")
        if set(test_ids) - self._store.active_test_ids(test_ids):
            raise ValidationFailed("Unknown or inactive test type")

        order = self._store.create_order(
            NewOrder(
                order_id=str(uuid.uuid4()),
                paciente_id=str(paciente_id),
                medico_id=medico_id,
                prioridad=prioridad,
                observaciones=observaciones,
                tipo_prueba_ids=test_ids,
            )
        )
        self._audit(actor, order.paciente_id, "orden_laboratorio", order.id, "crear")
        return order

    def get_order(self, actor: AccessProfile, order_id: str) -> LabOrder:
        order = self._readable_order(actor, order_id)
        return order

    # ---------- results ----------

    def record_results(
        self,
        actor: AccessProfile,
        order_id: str,
        detail_id: str,
        entries: list[ResultEntry],
    ) -> LabOrder:
        _require(actor, PERM_RESULT_REGISTER)
        order = self._store.get_order(order_id)
        if order is None:
            raise NotFound("Order not found")
        if not entries:
            raise ValidationFailed("At least one analyte result is required")
        for entry in entries:
            validate_result_values(
                nombre=entry.nombre_analito,
                valor_texto=entry.valor_texto,
                valor_numerico=entry.valor_numerico,
                unidad=entry.unidad,
            )
            if entry.analito_id is not None and not self._store.analyte_exists(entry.analito_id):
                raise ValidationFailed("Unknown analyte")
            if entry.analito_id is not None:
                # A result belongs to the test it is entered under. Analytes that were not part
                # of the order need a new order, never a silent extra row on this one.
                detail = next((d for d in order.details if d.id == str(detail_id)), None)
                if detail is not None and not self._store.analyte_linked_to_test(
                    entry.analito_id, detail.tipo_prueba_id
                ):
                    raise ValidationFailed("This analyte is not part of the ordered test")

        status = self._store.record_results(
            order_id,
            str(detail_id),
            self._classify(order, list(entries)),
            self._clock(),
            actor.user_id,
        )
        if status == "DETAIL_NOT_FOUND":
            raise NotFound("Order detail not found")
        if status == "DETAIL_ALREADY_COMPLETED":
            raise Conflict("This test already has results")
        if status == "ANALYTE_ALREADY_REPORTED":
            raise Conflict("An analyte of this test already has a result")
        self._audit(actor, order.paciente_id, "resultado_laboratorio", str(detail_id), "registrar")
        return self._store.get_order(order_id) or order

    def _classify(self, order: LabOrder, entries: list[ResultEntry]) -> list[ResultEntry]:
        """Snapshot the classification against the validated reference (E5 spec 7.2 step 2).

        Results without an analyte are stored unclassified. The category is only ever a snapshot
        of the reference applied at this moment; it is never recalculated later.
        """
        sex, birth = self._store.patient_profile(order.paciente_id)
        today = self._clock().date()
        age_days = None if birth is None else (today - birth).days
        classified = []
        for entry in entries:
            if entry.analito_id is None:
                classified.append(entry)
                continue
            references = (
                self._store.validated_references(entry.analito_id, entry.unidad, today)
                if entry.unidad
                else []
            )
            verdict = classify(
                None if entry.valor_numerico is None else Decimal(str(entry.valor_numerico)),
                entry.unidad,
                references,
                PatientContext(
                    sex,
                    age_days,
                    entry.metodo_analitico,
                    entry.tipo_muestra,
                    contexto=entry.contexto_muestra,
                ),
                today,
            )
            ref = verdict.reference
            classified.append(
                replace(
                    entry,
                    interpretacion=verdict.interpretacion,
                    referencia_id=ref.id if ref else None,
                    referencia_version=ref.version if ref else None,
                    referencia_min=float(ref.minimo) if ref and ref.minimo is not None else None,
                    referencia_max=float(ref.maximo) if ref and ref.maximo is not None else None,
                )
            )
        return classified

    def list_results(self, actor: AccessProfile, order_id: str) -> list[LabResult]:
        order = self._readable_order(actor, order_id)
        results = self._store.list_results(order.id)
        self._audit(actor, order.paciente_id, "resultado_laboratorio", order.id, "consultar")
        return results

    # ---------- medical review ----------

    def review(
        self,
        actor: AccessProfile,
        order_id: str,
        *,
        conclusion: str,
        recomendaciones: str | None,
    ) -> None:
        _require(actor, PERM_REVIEW)
        order = self._store.get_order(order_id)
        if order is None or order.medico_user_id != actor.user_id:
            raise Forbidden(_FORBIDDEN)
        validate_conclusion(conclusion, recomendaciones)

        status = self._store.record_review(
            order_id, conclusion, recomendaciones, self._clock(), actor.user_id
        )
        if status == "NOT_COMPLETED":
            raise Conflict("The order results are not complete yet")
        if status == "ALREADY_REVIEWED":
            raise Conflict("This order was already reviewed")
        self._audit(actor, order.paciente_id, "revision_laboratorio", order.id, "revisar")

    # ---------- documents ----------

    def upload_document(
        self,
        actor: AccessProfile,
        order_id: str,
        *,
        content: bytes,
        mime: str,
        filename: str,
        tipo_documento: str,
    ) -> StoredDocument:
        _require(actor, PERM_RESULT_REGISTER)
        order = self._store.get_order(order_id)
        if order is None:
            raise NotFound("Order not found")
        validate_pdf(content, mime)
        validate_document_type(tipo_documento)
        name = safe_filename(filename)

        key = self._storage.save(content)
        try:
            document = self._store.create_document(
                NewDocument(
                    document_id=str(uuid.uuid4()),
                    paciente_id=order.paciente_id,
                    orden_id=order.id,
                    subido_por_user_id=actor.user_id,
                    nombre_archivo=name,
                    ruta_storage=key,
                    hash_sha256=hashlib.sha256(content).hexdigest(),
                    tipo_mime=mime,
                    tamano_bytes=len(content),
                    tipo_documento=tipo_documento,
                )
            )
        except Exception:
            self._storage.delete(key)
            raise
        self._audit(actor, order.paciente_id, "documento_clinico", document.id, "subir")
        return document

    def download_document(
        self, actor: AccessProfile, document_id: str
    ) -> tuple[StoredDocument, bytes]:
        document = self._store.get_document(document_id)
        if document is None or not self._can_read_document(actor, document):
            # Denied reads are audited too (S11); the document may not even exist.
            self._audit(
                actor,
                document.paciente_id if document else None,
                "documento_clinico",
                document_id,
                "descargar",
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        content = self._storage.read(document.ruta_storage)
        if hashlib.sha256(content).hexdigest() != document.hash_sha256:
            self._audit(
                actor,
                document.paciente_id,
                "documento_clinico",
                document.id,
                "descargar",
                "INTEGRIDAD_FALLIDA",
            )
            raise DocumentIntegrityError("Document integrity check failed")
        self._audit(actor, document.paciente_id, "documento_clinico", document.id, "descargar")
        return document, content

    # ---------- helpers ----------

    def _readable_order(self, actor: AccessProfile, order_id: str) -> LabOrder:
        order = self._store.get_order(order_id)
        if order is None or not self._can_read_order(actor, order):
            self._audit(
                actor,
                order.paciente_id if order else None,
                "orden_laboratorio",
                order_id,
                "consultar",
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        return order

    @staticmethod
    def _can_read_order(actor: AccessProfile, order: LabOrder) -> bool:
        if PERM_RESULT_READ_OWN in actor.permissions and order.paciente_user_id == actor.user_id:
            return True
        return (
            PERM_RESULT_READ_CONTEXT in actor.permissions and order.medico_user_id == actor.user_id
        )

    @staticmethod
    def _can_read_document(actor: AccessProfile, document: StoredDocument) -> bool:
        if PERM_RESULT_READ_OWN in actor.permissions and document.paciente_user_id == actor.user_id:
            return True
        return (
            PERM_RESULT_READ_CONTEXT in actor.permissions
            and document.medico_user_id is not None
            and document.medico_user_id == actor.user_id
        )

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

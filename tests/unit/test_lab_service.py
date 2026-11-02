"""Laboratory use cases against in-memory fakes: orders, results, review, documents (E3)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import (
    LabDetail,
    LabOrder,
    LabResult,
    NewDocument,
    NewOrder,
    StoredDocument,
)
from medicalbooking.modules.laboratory.application.use_cases import (
    DocumentIntegrityError,
    LabService,
)
from medicalbooking.shared.domain.errors import Conflict, Forbidden, ValidationFailed

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.4\n% synthetic test report\n%%EOF\n"
HEMO = str(uuid.uuid4())
GLUC = str(uuid.uuid4())


def _id() -> str:
    return str(uuid.uuid4())


def _actor(*permissions: str, user_id: str | None = None) -> AccessProfile:
    return AccessProfile(
        user_id=user_id or _id(),
        status="ACTIVO",
        roles=frozenset(),
        permissions=frozenset(permissions),
    )


PACIENTE = ("resultados.leer_propios", "citas.reservar")
MEDICO = ("ordenes.crear", "resultados.leer_con_acceso", "revisiones.registrar")
LABORATORISTA = ("resultados.registrar",)


@dataclass
class FakeStore:
    """Honours the store contract without SQL. Concurrency is tested in integration."""

    patient_by_user: dict[str, str] = field(default_factory=dict)
    medico_by_user: dict[str, str] = field(default_factory=dict)
    active_tests: set[str] = field(default_factory=lambda: {HEMO, GLUC})
    orders: dict[str, LabOrder] = field(default_factory=dict)
    results: dict[str, list[LabResult]] = field(default_factory=dict)
    reviews: dict[str, str] = field(default_factory=dict)
    documents: dict[str, StoredDocument] = field(default_factory=dict)
    audit: list = field(default_factory=list)
    recorded_entries: list = field(default_factory=list)
    references: list = field(default_factory=list)
    profile: tuple = (None, None)

    def patient_profile(self, paciente_id):
        return self.profile

    def analyte_exists(self, analito_id):
        return not analito_id.startswith("desconocido")

    def validated_references(self, analito_id, unidad, on):
        return [r for r in self.references if r.id.startswith(analito_id)]

    def resolve_patient_id(self, user_id):
        return self.patient_by_user.get(user_id)

    def resolve_medico_id(self, user_id):
        return self.medico_by_user.get(user_id)

    def patient_exists(self, paciente_id):
        return paciente_id in self.patient_by_user.values()

    def active_test_ids(self, ids):
        return {i for i in ids if i in self.active_tests}

    def create_order(self, command: NewOrder) -> LabOrder:
        medico_user = next(u for u, m in self.medico_by_user.items() if m == command.medico_id)
        paciente_user = next(u for u, p in self.patient_by_user.items() if p == command.paciente_id)
        order = LabOrder(
            id=command.order_id,
            paciente_id=command.paciente_id,
            paciente_user_id=paciente_user,
            medico_id=command.medico_id,
            medico_user_id=medico_user,
            prioridad=command.prioridad,
            estado="SOLICITADA",
            observaciones=command.observaciones,
            details=tuple(
                LabDetail(id=_id(), tipo_prueba_id=t, estado="PENDIENTE")
                for t in command.tipo_prueba_ids
            ),
        )
        self.orders[order.id] = order
        return order

    def get_order(self, order_id):
        return self.orders.get(order_id)

    def record_results(self, order_id, detail_id, entries, now, actor_user_id):
        self.recorded_entries.extend(entries)
        order = self.orders[order_id]
        detail = next((d for d in order.details if d.id == detail_id), None)
        if detail is None:
            return "DETAIL_NOT_FOUND"
        if detail.estado != "PENDIENTE":
            return "DETAIL_ALREADY_COMPLETED"
        self.results.setdefault(order_id, []).extend(
            LabResult(
                id=_id(),
                detalle_id=detail_id,
                nombre_analito=e.nombre_analito,
                valor_texto=e.valor_texto,
                valor_numerico=e.valor_numerico,
                unidad=e.unidad,
            )
            for e in entries
        )
        details = tuple(
            LabDetail(id=d.id, tipo_prueba_id=d.tipo_prueba_id, estado="COMPLETADO")
            if d.id == detail_id
            else d
            for d in order.details
        )
        done = all(d.estado == "COMPLETADO" for d in details)
        self.orders[order_id] = LabOrder(
            **{
                **order.__dict__,
                "details": details,
                "estado": "COMPLETADA" if done else "EN_PROCESO",
            }
        )
        return "CREATED"

    def list_results(self, order_id):
        return list(self.results.get(order_id, []))

    def record_review(self, order_id, conclusion, recomendaciones, now, actor_user_id):
        order = self.orders[order_id]
        if order.estado != "COMPLETADA":
            return "NOT_COMPLETED"
        if order_id in self.reviews:
            return "ALREADY_REVIEWED"
        self.reviews[order_id] = conclusion
        return "CREATED"

    def create_document(self, command: NewDocument) -> StoredDocument:
        order = self.orders.get(command.orden_id) if command.orden_id else None
        document = StoredDocument(
            id=command.document_id,
            paciente_id=command.paciente_id,
            paciente_user_id=next(
                u for u, p in self.patient_by_user.items() if p == command.paciente_id
            ),
            orden_id=command.orden_id,
            medico_user_id=order.medico_user_id if order else None,
            nombre_archivo=command.nombre_archivo,
            ruta_storage=command.ruta_storage,
            hash_sha256=command.hash_sha256,
            tipo_mime=command.tipo_mime,
            tamano_bytes=command.tamano_bytes,
        )
        self.documents[document.id] = document
        return document

    def get_document(self, document_id):
        return self.documents.get(document_id)

    def record_audit(self, event):
        self.audit.append(event)


@dataclass
class FakeStorage:
    files: dict[str, bytes] = field(default_factory=dict)

    def save(self, content: bytes) -> str:
        key = f"{uuid.uuid4()}.bin"
        self.files[key] = content
        return key

    def read(self, key: str) -> bytes:
        return self.files[key]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def world(store):
    """One patient, one ordering doctor, one other patient, one other doctor."""
    patient = _actor(*PACIENTE)
    doctor = _actor(*MEDICO)
    other_patient = _actor(*PACIENTE)
    other_doctor = _actor(*MEDICO)
    store.patient_by_user[patient.user_id] = _id()
    store.patient_by_user[other_patient.user_id] = _id()
    store.medico_by_user[doctor.user_id] = _id()
    store.medico_by_user[other_doctor.user_id] = _id()
    return {
        "patient": patient,
        "doctor": doctor,
        "other_patient": other_patient,
        "other_doctor": other_doctor,
        "lab": _actor(*LABORATORISTA),
    }


def _service(store, storage) -> LabService:
    return LabService(store=store, storage=storage, clock=lambda: NOW)


def _order(service, world, *, tests=(HEMO,)):
    return service.create_order(
        world["doctor"],
        paciente_id=world_patient_id(world["patient"].user_id, service._store),
        prioridad="NORMAL",
        observaciones=None,
        tipo_prueba_ids=list(tests),
    )


def world_patient_id(user_id, store):
    return store.patient_by_user[user_id]


def _entry(**overrides):
    values = {
        "nombre_analito": "Hemoglobina",
        "valor_texto": None,
        "valor_numerico": 13.5,
        "unidad": "g/dL",
    }
    values.update(overrides)
    from medicalbooking.modules.laboratory.application.ports import ResultEntry

    return ResultEntry(**values)


# ---------- orders ----------


def test_only_a_doctor_with_permission_can_create_an_order(store, storage, world) -> None:
    service = _service(store, storage)

    with pytest.raises(Forbidden):
        service.create_order(
            world["patient"],
            paciente_id=world_patient_id(world["patient"].user_id, store),
            prioridad="NORMAL",
            observaciones=None,
            tipo_prueba_ids=[HEMO],
        )


def test_a_user_with_permission_but_no_doctor_profile_cannot_order(store, storage, world) -> None:
    nobody = _actor(*MEDICO)

    with pytest.raises(Forbidden):
        _service(store, storage).create_order(
            nobody,
            paciente_id=world_patient_id(world["patient"].user_id, store),
            prioridad="NORMAL",
            observaciones=None,
            tipo_prueba_ids=[HEMO],
        )


def test_order_starts_requested_with_pending_details(store, storage, world) -> None:
    order = _order(_service(store, storage), world, tests=(HEMO, GLUC))

    assert order.estado == "SOLICITADA"
    assert [d.estado for d in order.details] == ["PENDIENTE", "PENDIENTE"]


@pytest.mark.parametrize("tests", [[], ["tipo-inexistente"]])
def test_order_needs_known_active_test_types(store, storage, world, tests) -> None:
    with pytest.raises(ValidationFailed):
        _order(_service(store, storage), world, tests=tests)


def test_order_rejects_unknown_priority(store, storage, world) -> None:
    with pytest.raises(ValidationFailed):
        _service(store, storage).create_order(
            world["doctor"],
            paciente_id=world_patient_id(world["patient"].user_id, store),
            prioridad="INMEDIATA",
            observaciones=None,
            tipo_prueba_ids=[HEMO],
        )


def test_patient_reads_only_own_orders(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    assert service.get_order(world["patient"], order.id).id == order.id
    with pytest.raises(Forbidden):
        service.get_order(world["other_patient"], order.id)


def test_missing_and_forbidden_orders_answer_the_same(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(Forbidden) as missing:
        service.get_order(world["patient"], _id())
    with pytest.raises(Forbidden) as forbidden:
        service.get_order(world["other_patient"], order.id)

    assert missing.value.detail == forbidden.value.detail


def test_only_the_ordering_doctor_reads_the_order(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    assert service.get_order(world["doctor"], order.id).id == order.id
    with pytest.raises(Forbidden):
        service.get_order(world["other_doctor"], order.id)


# ---------- results ----------


def test_recording_one_detail_moves_the_order_to_in_progress(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world, tests=(HEMO, GLUC))

    updated = service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])

    assert updated.estado == "EN_PROCESO"
    assert updated.details[0].estado == "COMPLETADO"


def test_completing_every_detail_completes_the_order(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world, tests=(HEMO, GLUC))
    for detail in order.details:
        service.record_results(world["lab"], order.id, detail.id, [_entry()])

    assert store.orders[order.id].estado == "COMPLETADA"


def test_a_completed_detail_cannot_be_recorded_again(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])

    with pytest.raises(Conflict):
        service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])


def test_a_result_needs_a_text_or_numeric_value(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(ValidationFailed):
        service.record_results(
            world["lab"],
            order.id,
            order.details[0].id,
            [_entry(valor_numerico=None, valor_texto=None)],
        )


def test_results_never_carry_a_clinical_classification(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])

    # Without an analyte there is nothing to classify: the snapshot stays empty (E3 contract).
    assert all(entry.interpretacion is None for entry in store.recorded_entries)


def test_only_laboratory_staff_records_results(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(Forbidden):
        service.record_results(world["doctor"], order.id, order.details[0].id, [_entry()])


def test_a_result_for_a_known_analyte_is_classified_at_registration(store, storage, world) -> None:
    analito = "analito-hb"
    store.references = [
        Reference(
            id=f"{analito}-ref",
            version=1,
            unidad="g/dL",
            minimo=Decimal("12.0"),
            maximo=Decimal("17.5"),
            sexo=None,
            edad_min_dias=None,
            edad_max_dias=None,
            metodo=None,
            tipo_muestra=None,
            contexto=None,
            fecha_inicio=date(2020, 1, 1),
            fecha_fin=None,
        )
    ]
    service = _service(store, storage)
    order = _order(service, world)

    service.record_results(
        world["lab"],
        order.id,
        order.details[0].id,
        [_entry(analito_id=analito, valor_numerico=11.0)],
    )

    recorded = store.recorded_entries[-1]
    assert recorded.interpretacion == "BAJO"
    assert recorded.referencia_id == f"{analito}-ref"
    assert recorded.referencia_version == 1


def test_a_result_without_an_approved_reference_stays_unclassified(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    service.record_results(
        world["lab"],
        order.id,
        order.details[0].id,
        [_entry(analito_id="analito-sin-referencia", valor_numerico=11.0)],
    )

    assert store.recorded_entries[-1].interpretacion == "NO_EVALUABLE"
    assert store.recorded_entries[-1].referencia_id is None


def test_patient_reads_own_results_and_the_read_is_audited(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])

    results = service.list_results(world["patient"], order.id)

    assert len(results) == 1
    assert store.audit[-1].accion == "consultar"


# ---------- medical review ----------


def test_review_needs_a_completed_order(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(Conflict):
        service.review(world["doctor"], order.id, conclusion="Sin hallazgos", recomendaciones=None)


def test_review_is_recorded_once(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])
    service.review(world["doctor"], order.id, conclusion="Sin hallazgos", recomendaciones=None)

    with pytest.raises(Conflict):
        service.review(world["doctor"], order.id, conclusion="Otra", recomendaciones=None)


def test_only_the_ordering_doctor_reviews(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])

    with pytest.raises(Forbidden):
        service.review(world["other_doctor"], order.id, conclusion="x", recomendaciones=None)


def test_review_does_not_rewrite_the_results(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])
    before = list(store.results[order.id])

    service.review(world["doctor"], order.id, conclusion="Sin hallazgos", recomendaciones=None)

    assert store.results[order.id] == before


# ---------- documents ----------


def test_pdf_is_stored_with_its_sha256_and_a_generated_name(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    document = service.upload_document(
        world["lab"],
        order.id,
        content=PDF,
        mime="application/pdf",
        filename="../../secreto informe.pdf",
        tipo_documento="INFORME_LABORATORIO",
    )

    assert document.hash_sha256 == hashlib.sha256(PDF).hexdigest()
    assert ".." not in document.ruta_storage and "/" not in document.ruta_storage
    assert document.nombre_archivo == "secreto informe.pdf"


@pytest.mark.parametrize(
    ("content", "mime"),
    [
        (b"<html>not a pdf</html>", "application/pdf"),
        (PDF, "text/html"),
        (b"", "application/pdf"),
        (b"%PDF-" + b"x" * (10 * 1024 * 1024), "application/pdf"),
    ],
)
def test_upload_rejects_non_pdf_empty_and_oversized_files(
    store, storage, world, content, mime
) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(ValidationFailed):
        service.upload_document(
            world["lab"],
            order.id,
            content=content,
            mime=mime,
            filename="informe.pdf",
            tipo_documento="INFORME_LABORATORIO",
        )
    assert storage.files == {}


def test_only_laboratory_staff_uploads_documents(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(Forbidden):
        service.upload_document(
            world["doctor"],
            order.id,
            content=PDF,
            mime="application/pdf",
            filename="informe.pdf",
            tipo_documento="INFORME_LABORATORIO",
        )


def test_patient_downloads_own_document_and_the_bytes_match_the_hash(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    document = service.upload_document(
        world["lab"],
        order.id,
        content=PDF,
        mime="application/pdf",
        filename="informe.pdf",
        tipo_documento="INFORME_LABORATORIO",
    )

    found, content = service.download_document(world["patient"], document.id)

    assert found.id == document.id
    assert content == PDF
    assert store.audit[-1].accion == "descargar"


def test_other_patients_and_other_doctors_cannot_download(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    document = service.upload_document(
        world["lab"],
        order.id,
        content=PDF,
        mime="application/pdf",
        filename="informe.pdf",
        tipo_documento="INFORME_LABORATORIO",
    )

    with pytest.raises(Forbidden):
        service.download_document(world["other_patient"], document.id)
    with pytest.raises(Forbidden):
        service.download_document(world["other_doctor"], document.id)


def test_download_of_an_unknown_document_is_forbidden_like_a_foreign_one(
    store, storage, world
) -> None:
    with pytest.raises(Forbidden):
        _service(store, storage).download_document(world["patient"], _id())


def test_tampered_storage_fails_the_integrity_check(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    document = service.upload_document(
        world["lab"],
        order.id,
        content=PDF,
        mime="application/pdf",
        filename="informe.pdf",
        tipo_documento="INFORME_LABORATORIO",
    )
    storage.files[document.ruta_storage] = b"%PDF-tampered"

    with pytest.raises(DocumentIntegrityError):
        service.download_document(world["patient"], document.id)


def test_denied_document_download_is_audited_with_its_outcome(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)
    document = service.upload_document(
        world["lab"],
        order.id,
        content=PDF,
        mime="application/pdf",
        filename="informe.pdf",
        tipo_documento="INFORME_LABORATORIO",
    )

    with pytest.raises(Forbidden):
        service.download_document(world["other_patient"], document.id)

    assert store.audit[-1].resultado == "DENEGADO"
    assert store.audit[-1].entidad_id == document.id


def test_audit_carries_the_request_correlation_id(store, storage, world) -> None:
    service = LabService(
        store=store,
        storage=storage,
        clock=lambda: NOW,
        correlation_id=lambda: "7d3f8a2e-1b2c-4d5e-8f90-123456789abc",
    )
    order = _order(service, world)

    service.get_order(world["patient"], order.id)
    service.list_results(world["patient"], order.id)

    assert store.audit[-1].correlation_id == "7d3f8a2e-1b2c-4d5e-8f90-123456789abc"


def test_a_result_for_an_unknown_analyte_is_rejected_before_storage(store, storage, world) -> None:
    service = _service(store, storage)
    order = _order(service, world)

    with pytest.raises(ValidationFailed):
        service.record_results(
            world["lab"],
            order.id,
            order.details[0].id,
            [_entry(analito_id="desconocido-1", valor_numerico=11.0)],
        )
    assert store.recorded_entries == []

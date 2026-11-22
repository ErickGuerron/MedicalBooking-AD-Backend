"""Confirming an order generates its results PDF and stores it as a document of the order."""

# ruff: noqa: F811
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from medicalbooking.modules.laboratory.application.use_cases import LabService
from tests.unit.test_lab_service import (
    FakeStorage,
    FakeStore,
    _entry,
    _order,
    _service,
    world,  # noqa: F401  (fixture reused from the lab service tests)
)


class ConfirmStore(FakeStore):
    def confirm_order(self, order_id, now, actor_user_id):
        order = self.orders[order_id]
        if order.estado != "COMPLETADA":
            return "NOT_COMPLETED"
        if order.confirmada:
            return "ALREADY_CONFIRMED"
        self.orders[order_id] = replace(order, confirmada=True)
        return "CONFIRMED"

    def report_parties(self, order_id):
        return None

    def list_order_documents(self, order_id):
        return [d for d in self.documents.values() if d.orden_id == order_id]


@pytest.fixture
def store() -> ConfirmStore:
    return ConfirmStore()


def _completed(service: LabService, world):
    order = _order(service, world)
    service.record_results(world["lab"], order.id, order.details[0].id, [_entry()])
    return order


def test_confirming_stores_the_results_pdf_as_a_document_of_the_order(store, world) -> None:
    storage = FakeStorage()
    service = _service(store, storage)
    order = _completed(service, world)

    service.confirm_order(world["lab"], order.id)

    (document,) = store.list_order_documents(order.id)
    content = storage.files[document.ruta_storage]
    assert content.startswith(b"%PDF-")
    assert document.hash_sha256 == hashlib.sha256(content).hexdigest()
    assert document.tipo_mime == "application/pdf"
    assert document.nombre_archivo.startswith("resultados-")
    assert document.paciente_id == order.paciente_id


def test_the_patient_reads_the_generated_document_back(store, world) -> None:
    storage = FakeStorage()
    service = _service(store, storage)
    order = _completed(service, world)
    service.confirm_order(world["lab"], order.id)

    (listed,) = service.list_order_documents(world["patient"], order.id)
    _, content = service.download_document(world["patient"], listed.id)

    assert content.startswith(b"%PDF-")


def test_a_failing_storage_does_not_undo_the_confirmation(store, world) -> None:
    class BrokenStorage(FakeStorage):
        def save(self, content: bytes) -> str:
            raise OSError("mongo down")

    service = _service(store, BrokenStorage())
    order = _completed(service, world)

    service.confirm_order(world["lab"], order.id)

    assert store.orders[order.id].confirmada is True
    assert store.list_order_documents(order.id) == []


def test_a_repeated_confirmation_does_not_store_a_second_pdf(store, world) -> None:
    storage = FakeStorage()
    service = _service(store, storage)
    order = _completed(service, world)
    service.confirm_order(world["lab"], order.id)

    with pytest.raises(Exception, match="already confirmed"):
        service.confirm_order(world["lab"], order.id)

    assert len(store.list_order_documents(order.id)) == 1

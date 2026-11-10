"""Ports for laboratory. Adapters implement them; use cases never import SQL or files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from medicalbooking.modules.clinical.domain.references import Reference


@dataclass(frozen=True)
class LabDetail:
    id: str
    tipo_prueba_id: str
    estado: str


@dataclass(frozen=True)
class LabOrder:
    id: str
    paciente_id: str
    paciente_user_id: str
    medico_id: str
    medico_user_id: str
    prioridad: str
    estado: str
    observaciones: str | None
    details: tuple[LabDetail, ...]


@dataclass(frozen=True)
class LabResult:
    id: str
    detalle_id: str
    nombre_analito: str
    valor_texto: str | None
    valor_numerico: float | None
    unidad: str | None


@dataclass(frozen=True)
class ResultEntry:
    """A manually entered result. The classification fields are filled by the service (E5)."""

    nombre_analito: str
    valor_texto: str | None
    valor_numerico: float | None
    unidad: str | None
    analito_id: str | None = None
    metodo_analitico: str | None = None
    tipo_muestra: str | None = None
    contexto_muestra: str | None = None
    # Pregnancy state at sample time (S18): NO, T1, T2, T3. None means unknown.
    estado_embarazo: str | None = None
    interpretacion: str | None = None
    referencia_id: str | None = None
    referencia_version: int | None = None
    referencia_min: float | None = None
    referencia_max: float | None = None


@dataclass(frozen=True)
class NewOrder:
    order_id: str
    paciente_id: str
    medico_id: str
    prioridad: str
    observaciones: str | None
    tipo_prueba_ids: list[str]


@dataclass(frozen=True)
class StoredDocument:
    id: str
    paciente_id: str
    paciente_user_id: str
    orden_id: str | None
    medico_user_id: str | None
    nombre_archivo: str
    ruta_storage: str
    hash_sha256: str
    tipo_mime: str
    tamano_bytes: int


@dataclass(frozen=True)
class NewDocument:
    document_id: str
    paciente_id: str
    orden_id: str
    subido_por_user_id: str
    nombre_archivo: str
    ruta_storage: str
    hash_sha256: str
    tipo_mime: str
    tamano_bytes: int
    tipo_documento: str


@dataclass(frozen=True)
class AuditEvent:
    actor_user_id: str
    paciente_id: str | None
    entidad: str
    entidad_id: str
    accion: str
    resultado: str
    occurred_at: datetime
    correlation_id: str | None = None


class LabStore(Protocol):
    def analyte_exists(self, analito_id: str) -> bool:
        """True when the analyte is in the active knowledge catalog."""
        ...

    def analyte_linked_to_test(self, analito_id: str, tipo_prueba_id: str) -> bool:
        """True when the analyte is a part of this test (conocimiento_pruebas_analitos)."""
        ...

    def patient_profile(self, paciente_id: str) -> tuple[str | None, date | None]:
        """Return (sexo_registrado, fecha_nacimiento); None means unknown."""
        ...

    def validated_references(self, analito_id: str, unidad: str, on: date) -> list[Reference]:
        """Validated, currently valid references for this analyte and unit only."""
        ...

    def resolve_patient_id(self, user_id: str) -> str | None: ...

    def resolve_medico_id(self, user_id: str) -> str | None: ...

    def patient_exists(self, paciente_id: str) -> bool: ...

    def active_test_ids(self, ids: list[str]) -> set[str]: ...

    def create_order(self, command: NewOrder) -> LabOrder: ...

    def get_order(self, order_id: str) -> LabOrder | None: ...

    def record_results(
        self,
        order_id: str,
        detail_id: str,
        entries: list[ResultEntry],
        now: datetime,
        actor_user_id: str,
    ) -> str:
        """Return CREATED, DETAIL_NOT_FOUND or DETAIL_ALREADY_COMPLETED."""
        ...

    def list_results(self, order_id: str) -> list[LabResult]: ...

    def record_review(
        self,
        order_id: str,
        conclusion: str,
        recomendaciones: str | None,
        now: datetime,
        actor_user_id: str,
    ) -> str:
        """Return CREATED, NOT_COMPLETED or ALREADY_REVIEWED."""
        ...

    def create_document(self, command: NewDocument) -> StoredDocument: ...

    def get_document(self, document_id: str) -> StoredDocument | None: ...

    def record_audit(self, event: AuditEvent) -> None: ...


class DocumentStorage(Protocol):
    def save(self, content: bytes) -> str:
        """Store the bytes privately and return an opaque key (never a path from the client)."""
        ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

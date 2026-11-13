"""Patient results served from the read replicas during a SQL Server outage (spec 8.3, S8).

The audit record is written before the replica is read. If the audit cannot be written,
the read is rejected and the replica is never touched (fail closed). Only identifiers and
the outcome are audited, never clinical values.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class AccountNotActive(Exception):
    """The account is missing, blocked or not active in the replica. Nothing is served."""


class AccessDenied(Exception):
    """The account lacks the own-results permission, or the patient is not its own."""


class AuditUnavailable(Exception):
    """The audit store could not record the read; the read must not be served."""


@dataclass(frozen=True)
class AuditRecord:
    fecha_evento: datetime
    paciente_id: str
    actor_id: str
    accion: str
    entidad: str
    resultado: str
    correlation_id: str
    ip_origen: str | None

    def as_document(self) -> dict[str, object]:
        return {
            "fecha_evento": self.fecha_evento,
            "paciente_id": self.paciente_id,
            "actor_id": self.actor_id,
            "accion": self.accion,
            "entidad": self.entidad,
            "resultado": self.resultado,
            "correlation_id": self.correlation_id,
            "ip_origen": self.ip_origen,
        }


@dataclass(frozen=True)
class PatientResults:
    patient_id: UUID
    orders: tuple[object, ...]


class AuditSink(Protocol):
    def append(self, record: AuditRecord) -> None: ...


class ReplicaReader(Protocol):
    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults: ...


class FallbackReadService:
    def __init__(
        self,
        sink: AuditSink,
        reader: ReplicaReader,
        clock: Callable[[], datetime],
    ) -> None:
        self._sink = sink
        self._reader = reader
        self._clock = clock

    def results_for(
        self,
        actor_id: UUID,
        patient_id: UUID,
        correlation_id: UUID,
        ip: str | None,
    ) -> PatientResults:
        self._sink.append(
            AuditRecord(
                fecha_evento=self._clock(),
                paciente_id=str(patient_id),
                actor_id=str(actor_id),
                accion="READ_CONTINGENCY",
                entidad="resultados_laboratorio",
                resultado="AUTORIZADA",
                correlation_id=str(correlation_id),
                ip_origen=ip,
            )
        )
        return self._reader.results_of(patient_id, actor_id)

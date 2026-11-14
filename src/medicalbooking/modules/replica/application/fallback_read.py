"""Patient results served from the read replicas during a SQL Server outage (spec 8.3, S8).

Order of a fallback read, and why:
1. The patient is resolved from the account: the account must be ACTIVO and hold the
   own-results permission. Nothing about the patient is read before this check.
2. The audit record is written. If it cannot be written, the read is rejected (fail closed).
3. Only then the results are read from the replica, which checks the same rule again.

A denied request is never audited as authorized. Only identifiers and the outcome are
audited, never clinical values.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class AccountNotActive(Exception):
    """The account is missing, blocked or not active in the replica. Nothing is served."""


class AccessDenied(Exception):
    """The account lacks the own-results permission, or no patient belongs to it."""


class AuditUnavailable(Exception):
    """The audit store could not record the read; the read must not be served."""


@dataclass(frozen=True)
class AuditRecord:
    fecha_evento: datetime
    paciente_id: str | None
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


class PatientResolver(Protocol):
    def patient_for(self, usuario_id: UUID) -> UUID: ...


class ReplicaReader(Protocol):
    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults: ...


class FallbackReadService:
    def __init__(
        self,
        sink: AuditSink,
        reader: ReplicaReader,
        resolver: PatientResolver,
        clock: Callable[[], datetime],
    ) -> None:
        self._sink = sink
        self._reader = reader
        self._resolver = resolver
        self._clock = clock

    def results_for(self, actor_id: UUID, correlation_id: UUID, ip: str | None) -> PatientResults:
        try:
            patient_id = self._resolver.patient_for(actor_id)
        except (AccessDenied, AccountNotActive):
            self._audit_denial(actor_id, correlation_id, ip)
            raise
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

    def _audit_denial(self, actor_id: UUID, correlation_id: UUID, ip: str | None) -> None:
        """Record a refused attempt without patient data. A failed audit keeps the refusal."""
        try:
            self._sink.append(
                AuditRecord(
                    fecha_evento=self._clock(),
                    paciente_id=None,
                    actor_id=str(actor_id),
                    accion="READ_CONTINGENCY",
                    entidad="resultados_laboratorio",
                    resultado="DENEGADA",
                    correlation_id=str(correlation_id),
                    ip_origen=ip,
                )
            )
        except AuditUnavailable as error:
            # The refusal stands. Log the error type only: the gap must be visible.
            logger.error("denial not audited: %s", type(error).__name__)

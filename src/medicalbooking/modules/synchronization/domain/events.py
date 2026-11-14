"""Outbox event as consumers see it: the contract between the dispatcher and its handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class OutboxEvent:
    id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    aggregate_version: int
    created_at: datetime
    correlation_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)

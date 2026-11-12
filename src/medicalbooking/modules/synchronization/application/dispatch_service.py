"""Outbox dispatch rules: materialize deliveries, then deliver them in per-aggregate order."""

from __future__ import annotations

from medicalbooking.modules.synchronization.application.ports import OutboxStore


class OutboxDispatchService:
    def __init__(self, store: OutboxStore) -> None:
        self._store = store

    def materialize(self) -> int:
        return self._store.materialize()

"""Replica reconciliation planning: pure rules, no database and no network.

A latest event is expected to be present in a replica unless its type deletes the aggregate.
Only the destinations listed in the presence map are checked, and each one is independent.
"""

from __future__ import annotations

from medicalbooking.modules.synchronization.domain.reconciliation import (
    LatestEvent,
    Repair,
    plan_repairs,
)

EVENT = "11111111-1111-1111-1111-111111111111"
AGGREGATE = "22222222-2222-2222-2222-222222222222"
MONGODB = "MONGODB"
KAFKA = "KAFKA"


def _latest(
    event_type: str = "usuario.creado",
    aggregate_type: str = "usuario",
    aggregate_id: str = AGGREGATE,
    event_id: str = EVENT,
) -> LatestEvent:
    return LatestEvent(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
    )


def _present(destination: str, aggregate_type: str, *ids: str) -> dict:
    return {(destination, aggregate_type): frozenset(ids)}


def test_present_and_expected_needs_no_repair() -> None:
    presence = _present(MONGODB, "usuario", AGGREGATE)

    assert plan_repairs([_latest()], presence) == []


def test_missing_and_expected_is_repaired_for_that_destination() -> None:
    presence = _present(MONGODB, "usuario")

    assert plan_repairs([_latest()], presence) == [
        Repair(event_id=EVENT, destination_code=MONGODB, reason="missing"),
    ]


def test_present_after_a_delete_is_repaired() -> None:
    presence = _present(MONGODB, "usuario", AGGREGATE)

    plan = plan_repairs([_latest(event_type="usuario.eliminado")], presence)

    assert plan == [Repair(event_id=EVENT, destination_code=MONGODB, reason="present_after_delete")]


def test_absent_after_a_delete_needs_no_repair() -> None:
    presence = _present(MONGODB, "usuario")

    assert plan_repairs([_latest(event_type="usuario.eliminado")], presence) == []


def test_cita_delete_follows_the_same_rule() -> None:
    event = _latest(event_type="cita.eliminada", aggregate_type="cita")

    assert plan_repairs([event], _present(MONGODB, "cita", AGGREGATE)) == [
        Repair(event_id=EVENT, destination_code=MONGODB, reason="present_after_delete"),
    ]
    assert plan_repairs([event], _present(MONGODB, "cita")) == []


def test_untracked_aggregate_types_are_ignored() -> None:
    event = _latest(event_type="orden_laboratorio.creada", aggregate_type="orden_laboratorio")
    presence = _present(MONGODB, "orden_laboratorio")

    assert plan_repairs([event], presence) == []


def test_each_destination_is_checked_independently() -> None:
    presence = {
        (MONGODB, "usuario"): frozenset(),
        (KAFKA, "usuario"): frozenset({AGGREGATE}),
    }

    assert plan_repairs([_latest()], presence) == [
        Repair(event_id=EVENT, destination_code=MONGODB, reason="missing"),
    ]


def test_unchecked_destination_produces_no_repair() -> None:
    # A destination absent from the presence map was skipped (for example Supabase without
    # its reconciler connection), so it is never repaired.
    assert plan_repairs([_latest()], _present(MONGODB, "usuario")) == [
        Repair(event_id=EVENT, destination_code=MONGODB, reason="missing"),
    ]
    assert plan_repairs([_latest()], {}) == []

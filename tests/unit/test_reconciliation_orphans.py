"""Hard-delete detection: replica ids with no source row are orphans that need a tombstone."""

from __future__ import annotations

from medicalbooking.modules.synchronization.domain.reconciliation import plan_orphans


def test_a_replica_id_missing_from_the_source_is_an_orphan() -> None:
    orphans = plan_orphans(
        source_ids=frozenset({"a", "b"}),
        replica_ids=frozenset({"a", "b", "c"}),
    )

    assert orphans == frozenset({"c"})


def test_ids_present_in_both_are_not_orphans() -> None:
    assert plan_orphans(frozenset({"a"}), frozenset({"a"})) == frozenset()


def test_a_source_row_not_yet_replicated_is_not_an_orphan() -> None:
    # Creation waits in the outbox; the replica simply lacks it. That is not a delete.
    assert plan_orphans(frozenset({"a", "new"}), frozenset({"a"})) == frozenset()


def test_an_empty_replica_has_no_orphans() -> None:
    assert plan_orphans(frozenset({"a"}), frozenset()) == frozenset()

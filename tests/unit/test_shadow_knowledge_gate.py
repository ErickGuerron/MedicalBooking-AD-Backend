"""D1: a database whose clinical knowledge differs from the bundle is refused (409), not evaluated.

Fake store, shared with the shadow wiring tests.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from medicalbooking.modules.clinical.application.shadow_findings import ShadowFindingsService
from medicalbooking.modules.clinical.domain.knowledge_bundle import BUNDLE_DIGEST
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Conflict, Forbidden
from tests.unit.test_shadow_vcm_wiring import ACTOR, FakeStore, vcm_reference

OTHER_KNOWLEDGE = "0" * 64


def _service(store: FakeStore, digest: str) -> ShadowFindingsService:
    return ShadowFindingsService(
        store,
        clock=lambda: datetime(2026, 11, 9, 12, 0),
        knowledge_digest=lambda: digest,
    )


def test_a_database_with_other_knowledge_is_refused_and_nothing_is_audited() -> None:
    store = FakeStore([vcm_reference()])

    with pytest.raises(Conflict, match="bundle"):
        _service(store, OTHER_KNOWLEDGE).get(ACTOR, "orden-1")

    assert store.audit == []


def test_the_refusal_comes_after_authorization() -> None:
    without_permissions = AccessProfile(
        user_id="medico-1",
        status="ACTIVO",
        roles=frozenset({"MEDICO"}),
        permissions=frozenset(),
    )

    with pytest.raises(Forbidden):
        _service(FakeStore([vcm_reference()]), OTHER_KNOWLEDGE).get(without_permissions, "orden-1")


def test_a_database_with_the_bundle_knowledge_is_evaluated() -> None:
    report = _service(FakeStore([vcm_reference()]), BUNDLE_DIGEST).get(ACTOR, "orden-1")

    assert report is not None


def test_the_default_expected_digest_is_the_generated_bundle_digest() -> None:
    service = ShadowFindingsService(
        FakeStore([vcm_reference()]),
        clock=lambda: datetime(2026, 11, 9, 12, 0),
        knowledge_digest=lambda: BUNDLE_DIGEST,
    )

    assert service.get(ACTOR, "orden-1") is not None

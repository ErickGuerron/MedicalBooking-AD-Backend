"""Fixtures shared by the billing and checkout integration tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from tests.integration.checkout_support import seed_factura


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    return migrated_engine


@pytest.fixture
def factura(engine: Engine) -> Iterator[str]:
    """A pending invoice inserted directly into the migrated database.

    The insert is committed before the test starts. Holding it open across the test body made
    the test's own connections wait on a lock that only the test's teardown could release.
    """
    with engine.begin() as conn:
        factura_id = seed_factura(conn)
    yield factura_id

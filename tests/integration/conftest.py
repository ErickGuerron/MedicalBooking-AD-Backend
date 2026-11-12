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
    """A pending invoice inserted directly into the migrated database."""
    with engine.begin() as conn:
        yield seed_factura(conn)

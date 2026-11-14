"""The seed reads the target database name from both URL forms (plain and odbc_connect)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote_plus

import pytest

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo.py"


@pytest.fixture(scope="module")
def seed():
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plain_url_gives_the_database_path(seed) -> None:
    assert seed.database_name("postgresql+psycopg://user:pw@host:5432/Medilo") == "Medilo"


def test_odbc_connect_url_gives_the_database_keyword(seed) -> None:
    odbc = (
        "Driver={ODBC Driver 18 for SQL Server};Server=tcp:melido-server.database.windows.net,1433;"
        "Database=Medilo;Uid=manager;Pwd=secret;Encrypt=yes;TrustServerCertificate=no;"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + quote_plus(odbc)

    assert seed.database_name(url) == "Medilo"


def test_a_url_without_a_database_gives_an_empty_name(seed) -> None:
    assert seed.database_name("mssql+pyodbc:///?odbc_connect=Driver%3Dx%3BServer%3Dy") == ""

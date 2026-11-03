"""Reference ranges CSV: every rule that refuses a row, and the file-level refusal."""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.application.reference_csv import COLUMNS, parse_references

ANALITO = "a0000000-0000-4000-8000-00000000b001"
HEADER = ",".join(COLUMNS)


def _csv(*rows: str) -> str:
    return HEADER + "\n" + "\n".join(rows) + "\n"


def _row(**overrides: str) -> str:
    values = {
        "analito_id": ANALITO,
        "pmid": "26882804",
        "unidad": "g/dL",
        "minimo": "12.0",
        "maximo": "17.5",
        "sexo": "",
        "edad_min_dias": "",
        "edad_max_dias": "",
        "metodo": "",
        "tipo_muestra": "",
        "contexto": "",
        "fecha_inicio": "2015-01-01",
        "fecha_fin": "",
    }
    values.update(overrides)
    return ",".join(values[column] for column in COLUMNS)


def test_a_valid_row_is_accepted_with_its_pmid() -> None:
    result = parse_references(_csv(_row()))

    assert result.errors == ()
    assert result.rows[0].pmid == "26882804"
    assert str(result.rows[0].minimo) == "12.0"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"pmid": ""}, "pmid is required"),
        ({"pmid": "PMID-26882804"}, "pmid is required"),
        ({"analito_id": "no-es-uuid"}, "analito_id must be a UUID"),
        ({"unidad": ""}, "unidad is required"),
        ({"minimo": "", "maximo": ""}, "at least one of minimo or maximo"),
        ({"minimo": "18", "maximo": "12"}, "minimo is greater than maximo"),
        ({"minimo": "abc"}, "minimo is not a number"),
        ({"minimo": "NaN"}, "minimo must be finite"),
        ({"edad_min_dias": "-1"}, "edad_min_dias must be a whole number"),
        ({"edad_min_dias": "3650", "edad_max_dias": "365"}, "edad_min_dias is greater"),
        ({"fecha_inicio": ""}, "fecha_inicio is required"),
        ({"fecha_inicio": "01/01/2015"}, "fecha_inicio must be a date"),
        ({"fecha_fin": "2014-12-31"}, "fecha_fin is before fecha_inicio"),
    ],
)
def test_each_invalid_value_is_reported_with_its_line(override, message) -> None:
    result = parse_references(_csv(_row(**override)))

    assert result.errors, "an invalid row must produce an error"
    assert any(message in error and "line 2" in error for error in result.errors), result.errors


def test_every_problem_in_the_file_is_reported_not_only_the_first() -> None:
    result = parse_references(_csv(_row(pmid=""), _row(unidad=""), _row()))

    lines = {error.split(":")[0] for error in result.errors}
    assert lines == {"line 2", "line 3"}


def test_a_wrong_header_is_refused() -> None:
    result = parse_references("analito,pmid\n" + _row() + "\n")

    assert result.errors and "header must be exactly" in result.errors[0]


def test_an_empty_file_is_refused() -> None:
    result = parse_references(_csv())

    assert result.errors == ("the file has no reference rows",)


def test_the_template_alone_is_refused_until_a_clinician_adds_rows() -> None:
    assert parse_references(HEADER + "\n").errors == ("the file has no reference rows",)

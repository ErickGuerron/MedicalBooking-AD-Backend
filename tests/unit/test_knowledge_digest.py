from datetime import date, datetime
from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.knowledge_digest import (
    TABLES,
    canonical_value,
    knowledge_digest,
)


def _empty_tables() -> dict[str, list[dict]]:
    return {spec.name: [] for spec in TABLES}


def _sample() -> dict[str, list[dict]]:
    tables = _empty_tables()
    tables["fuentes"] = [
        {
            "codigo": "WHO-FERRITIN-2020",
            "nombre": "WHO 2020",
            "organizacion": "OMS",
            "url": None,
            "identificador_publicacion": "978-92-4-000012-4",
            "version_publicacion": "2020",
            "fecha_publicacion": date(2020, 4, 1),
            "fecha_consulta": datetime(2026, 1, 2, 10, 0),
            "licencia": "CC BY-NC-SA 3.0 IGO",
            "vigente": True,
        },
        {
            "codigo": "ADA-2026",
            "nombre": "ADA 2026",
            "organizacion": "ADA",
            "url": "https://doi.org/10.2337/dc26-s002",
            "identificador_publicacion": None,
            "version_publicacion": "2026",
            "fecha_publicacion": None,
            "fecha_consulta": None,
            "licencia": None,
            "vigente": True,
        },
    ]
    tables["analitos"] = [
        {
            "codigo_loinc": "718-7",
            "nombre": "Hemoglobina",
            "descripcion": None,
            "unidad_canonica": "g/dL",
            "tipo_valor": "NUMERICO",
            "activo": True,
        }
    ]
    tables["referencias"] = [
        {
            "analito": "718-7",
            "fuente": "ADA-2026",
            "version": 1,
            "unidad": "g/dL",
            "sexo_aplicable": "M",
            "edad_min_dias": 5475,
            "edad_max_dias": None,
            "tipo_muestra": None,
            "metodo_analitico": None,
            "contexto": None,
            "referencia_min": Decimal("13.0"),
            "referencia_max": Decimal("17.5"),
            "valor_texto_esperado": None,
            "fecha_inicio": date(2026, 1, 1),
            "fecha_fin": None,
            "estado": "VALIDADA",
        }
    ]
    return tables


def test_digest_is_the_same_whatever_the_row_order() -> None:
    tables = _sample()
    reversed_tables = {name: list(reversed(rows)) for name, rows in tables.items()}

    assert knowledge_digest(tables).digest == knowledge_digest(reversed_tables).digest


def test_digest_ignores_audit_columns_and_database_identifiers() -> None:
    tables = _sample()
    with_audit = {name: [dict(row) for row in rows] for name, rows in tables.items()}
    with_audit["referencias"][0]["validado_por"] = "5f1c0c3e-0000-0000-0000-000000000001"
    with_audit["referencias"][0]["id"] = "5f1c0c3e-0000-0000-0000-000000000002"

    assert knowledge_digest(tables).digest == knowledge_digest(with_audit).digest


def test_a_changed_reference_value_changes_the_digest() -> None:
    tables = _sample()
    changed = {name: [dict(row) for row in rows] for name, rows in tables.items()}
    changed["referencias"][0]["referencia_min"] = Decimal("12.9")

    assert knowledge_digest(tables).digest != knowledge_digest(changed).digest


def test_decimal_spelling_does_not_change_the_digest() -> None:
    assert canonical_value(Decimal("13.0")) == canonical_value(Decimal("13"))
    assert canonical_value(Decimal("0.00")) == "0"
    assert canonical_value(Decimal("1.50")) == "1.5"


def test_dates_are_written_as_iso_strings() -> None:
    assert canonical_value(date(2026, 1, 1)) == "2026-01-01"
    assert canonical_value(datetime(2026, 1, 2, 10, 0)) == "2026-01-02T10:00:00"


def test_unsupported_value_types_are_refused() -> None:
    with pytest.raises(TypeError):
        canonical_value(object())


def test_a_duplicate_natural_key_is_refused() -> None:
    tables = _sample()
    tables["analitos"].append(dict(tables["analitos"][0]))

    with pytest.raises(ValueError, match="duplicate natural key"):
        knowledge_digest(tables)


def test_a_missing_table_is_refused_not_treated_as_empty() -> None:
    tables = _sample()
    del tables["contenidos"]

    with pytest.raises(KeyError, match="contenidos"):
        knowledge_digest(tables)


def test_a_missing_field_is_refused() -> None:
    tables = _sample()
    del tables["analitos"][0]["unidad_canonica"]

    with pytest.raises(KeyError, match="unidad_canonica"):
        knowledge_digest(tables)


def test_counts_report_every_table() -> None:
    result = knowledge_digest(_sample())

    assert result.counts["fuentes"] == 2
    assert result.counts["analitos"] == 1
    assert result.counts["referencias"] == 1
    assert result.counts["reglas"] == 0
    assert set(result.counts) == {spec.name for spec in TABLES}


def test_an_empty_knowledge_base_has_a_stable_digest() -> None:
    assert knowledge_digest(_empty_tables()).digest == knowledge_digest(_empty_tables()).digest

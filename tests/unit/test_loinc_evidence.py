"""Evidence per clinical role (feature doc S9): result id, value and unit of each finding.

Same selection rules as roles_from_loinc: a wrong unit is not used, and two results for one role are
refused, never silently chosen.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.loinc_roles import AmbiguousResult, evidence_by_role


def test_evidence_keeps_the_result_id_value_and_unit_of_each_role() -> None:
    evidence = evidence_by_role(
        [
            ("res-1", "718-7", "g/dL", Decimal("10.5")),
            ("res-2", "2276-4", "ug/L", Decimal("8")),
        ]
    )

    assert evidence["hemoglobina"].resultado_id == "res-1"
    assert evidence["hemoglobina"].valor == Decimal("10.5")
    assert evidence["hemoglobina"].unidad == "g/dL"
    assert evidence["ferritina"].resultado_id == "res-2"


def test_a_result_in_another_unit_is_not_evidence() -> None:
    evidence = evidence_by_role([("res-1", "718-7", "g/L", Decimal("105"))])

    assert "hemoglobina" not in evidence


def test_two_results_for_one_role_are_refused() -> None:
    with pytest.raises(AmbiguousResult):
        evidence_by_role(
            [
                ("res-1", "718-7", "g/dL", Decimal("10")),
                ("res-2", "718-7", "g/dL", Decimal("11")),
            ]
        )


def test_an_unmapped_code_is_ignored() -> None:
    assert evidence_by_role([("res-1", "9999-9", "g/dL", Decimal("1"))]) == {}

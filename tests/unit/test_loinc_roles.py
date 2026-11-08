"""LOINC codes to clinical roles for the shadow findings (feature doc S13, decision L24: LOINC).

Codes verified in the LOINC catalogue (loinc.org): 718-7 hemoglobin in blood; 2276-4 ferritin in
serum or plasma; 1988-5 C-reactive protein in serum or plasma; 1558-6 fasting glucose in serum or
plasma; 4548-4 HbA1c, NGSP (percent). The IFCC HbA1c code 59261-8 is NOT mapped: its unit is
mmol/mol and the diabetes thresholds are in percent. Unverified codes (OGTT, random glucose) are
not mapped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from medicalbooking.modules.clinical.domain.loinc_roles import AmbiguousResult, roles_from_loinc


def d(value: str) -> Decimal:
    return Decimal(value)


def test_verified_codes_map_to_their_roles() -> None:
    roles = roles_from_loinc(
        [
            ("718-7", "g/dL", d("10.5")),
            ("2276-4", "ug/L", d("12")),
            ("1988-5", "mg/L", d("3.1")),
            ("1558-6", "mg/dL", d("126")),
            ("4548-4", "%", d("6.6")),
        ]
    )

    assert roles == {
        "hemoglobina": d("10.5"),
        "ferritina": d("12"),
        "pcr": d("3.1"),
        "glucosa_ayuno": d("126"),
        "hba1c": d("6.6"),
    }


def test_ifcc_hba1c_is_not_used_for_percent_thresholds() -> None:
    assert roles_from_loinc([("59261-8", "mmol/mol", d("48"))]) == {}


def test_unverified_or_unrelated_codes_are_ignored() -> None:
    assert roles_from_loinc([("2339-0", "mg/dL", d("95")), ("9999-9", "x", d("1"))]) == {}


def test_a_result_in_another_unit_is_not_used() -> None:
    # A CRP reported in mg/dL would be read as mg/L: ten times too low. It must not be used.
    assert roles_from_loinc([("1988-5", "mg/dL", d("0.3"))]) == {}


def test_two_results_for_the_same_role_are_ambiguous() -> None:
    with pytest.raises(AmbiguousResult):
        roles_from_loinc([("4548-4", "%", d("6.5")), ("4548-4", "%", d("6.6"))])

"""LOINC codes to clinical roles for the shadow findings (feature doc S13). Pure functions.

Only codes verified in the LOINC catalogue are mapped:
  718-7   Hemoglobin [Mass/volume] in Blood
  2276-4  Ferritin [Mass/volume] in Serum or Plasma
  1988-5  C reactive protein [Mass/volume] in Serum or Plasma
  1558-6  Fasting glucose [Mass/volume] in Serum or Plasma
  4548-4  Hemoglobin A1c/Hemoglobin.total in Blood (NGSP, percent)
Not mapped on purpose: 59261-8 (IFCC HbA1c, mmol/mol) and any code not verified here.
Two results for one role in the same order are ambiguous and are refused, never silently chosen.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

ROLE_BY_LOINC = {
    "718-7": "hemoglobina",
    "2276-4": "ferritina",
    "1988-5": "pcr",
    "1558-6": "glucosa_ayuno",
    "4548-4": "hba1c",
}
UNIT_BY_ROLE = {
    "hemoglobina": "g/dL",
    "ferritina": "ug/L",
    "pcr": "mg/L",
    "glucosa_ayuno": "mg/dL",
    "hba1c": "%",
}


class AmbiguousResult(ValueError):
    """Two results map to the same clinical role; the caller must ask a person which one counts."""


def roles_from_loinc(results: Iterable[tuple[str, str | None, Decimal]]) -> dict[str, Decimal]:
    """Map (loinc, unit, value) triples to clinical roles.

    A result in a unit other than the one the role's thresholds use is not used: a silent unit
    error would classify the patient wrongly.
    """
    roles: dict[str, Decimal] = {}
    for loinc, unit, value in results:
        role = ROLE_BY_LOINC.get(loinc)
        if role is None or unit != UNIT_BY_ROLE[role]:
            continue
        if role in roles:
            raise AmbiguousResult(f"two results for {role} ({loinc})")
        roles[role] = value
    return roles

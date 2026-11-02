"""Barrel for the clinical domain: import rules from here instead of the submodules.

Only pure domain modules are re-exported. Application and infrastructure are not, so the
laboratory module can import this package without creating an import cycle.
"""

from medicalbooking.modules.clinical.domain.evaluation import (
    MOTOR_VERSION,
    CriterionSpec,
    Evaluation,
    Evidence,
    Finding,
    ResultInput,
    RuleSpec,
    evaluate,
)
from medicalbooking.modules.clinical.domain.references import (
    ALTO,
    BAJO,
    DENTRO,
    NO_EVALUABLE,
    Classification,
    PatientContext,
    Reference,
    classify,
    overlaps,
)

__all__ = [
    "ALTO",
    "BAJO",
    "DENTRO",
    "MOTOR_VERSION",
    "NO_EVALUABLE",
    "Classification",
    "CriterionSpec",
    "Evaluation",
    "Evidence",
    "Finding",
    "PatientContext",
    "Reference",
    "ResultInput",
    "RuleSpec",
    "classify",
    "evaluate",
    "overlaps",
]

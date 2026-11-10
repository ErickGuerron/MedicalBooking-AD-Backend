"""Import plan: everything is fetched before anything is written (no network, fakes only)."""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.application.knowledge_import import build_plan
from medicalbooking.modules.clinical.infrastructure.external_sources import (
    Article,
    ExternalSourceError,
    Topic,
)

HB = "a0000000-0000-4000-8000-00000000b001"
CONFIG = {
    "analytes": [
        {"analito_id": HB, "loinc": "718-7", "expected_display": "Hemoglobin", "medlineplus": True}
    ],
    "rules": [
        {"regla_codigo": "HB-BAJA-SINT", "icd_search": "anemia", "pubmed_pmids": ["42969393"]}
    ],
}


class Loinc:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def lookup(self, code, expected_display):
        if self.fail:
            raise ExternalSourceError("LOINC", "code_mismatch")
        return "Hemoglobin [Mass/volume] in Blood"


class MedlinePlus:
    def topics(self, loinc_code):
        return [Topic("Hemoglobina", "https://medlineplus.gov/spanish/hemoglobin.html")]


class PubMed:
    def details(self, pmids):
        return [
            Article(pmid, "Hemoglobin ranges", "Test J", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            for pmid in pmids
        ]


class Icd11:
    def resolve(self, term):
        return "3A9Z", "Anemia", "https://id.who.int/icd/release/11/2024-01/mms/224336967"


def _plan(config=CONFIG, **overrides):
    clients = {"loinc": Loinc(), "medlineplus": MedlinePlus(), "pubmed": PubMed(), "icd11": Icd11()}
    clients.update(overrides)
    return build_plan(config, **clients)


def test_the_plan_contains_every_fetched_item_with_its_provenance() -> None:
    plan = _plan()

    assert plan.analyte_codes[0].loinc == "718-7"
    assert plan.contents[0].url == "https://medlineplus.gov/spanish/hemoglobin.html"
    assert {row.codigo for row in plan.sources} == {"PMID-42969393", "MEDLINEPLUS"}
    assert plan.rule_sources[0].source_codigo == "PMID-42969393"
    assert plan.diagnoses[0].code == "3A9Z"


def test_one_failing_source_means_no_plan_at_all() -> None:
    with pytest.raises(ExternalSourceError):
        _plan(loinc=Loinc(fail=True))


def test_a_source_that_is_not_configured_is_never_called() -> None:
    class MustNotBeCalled:
        def resolve(self, term):
            raise AssertionError("ICD-11 must not be called without an icd_search term")

    config = {
        "analytes": [],
        "rules": [{"regla_codigo": "GLU-FUERA-SINT", "icd_search": None, "pubmed_pmids": []}],
    }

    plan = build_plan(
        config,
        loinc=Loinc(),
        medlineplus=MedlinePlus(),
        pubmed=PubMed(),
        icd11=MustNotBeCalled(),
    )

    assert plan.diagnoses == ()
    assert plan.sources == ()


def test_the_real_clients_implement_the_methods_the_plan_calls() -> None:
    """Guards against the interface drifting: a renamed method must fail here, not at runtime."""
    import inspect

    from medicalbooking.modules.clinical.infrastructure.external_sources import (
        Icd11,
        Loinc,
        MedlinePlus,
        PubMed,
    )

    expected = {
        Loinc: ("lookup",),
        MedlinePlus: ("topics",),
        PubMed: ("details",),
        Icd11: ("resolve",),
    }
    for cls, methods in expected.items():
        for name in methods:
            assert callable(getattr(cls, name, None)), f"{cls.__name__}.{name} is missing"
    assert len(inspect.signature(Icd11.resolve).parameters) == 2  # self, term

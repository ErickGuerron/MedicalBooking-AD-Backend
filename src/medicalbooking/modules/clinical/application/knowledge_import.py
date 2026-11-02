"""Knowledge import plan: fetch everything from the external sources, write nothing.

The plan is built in full before any database write. If one source fails, the plan is never
returned, so the database is not touched at all. Every row the plan writes is a draft or a
link with provenance; published knowledge is never changed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from medicalbooking.modules.clinical.infrastructure.external_sources import (
    Article,
    ExternalSourceError,
    Topic,
)

MEDLINEPLUS_SOURCE = "MEDLINEPLUS"


class TerminologyClient(Protocol):
    def lookup(self, code: str, expected_display: str) -> str: ...


class EducationClient(Protocol):
    def topics(self, loinc_code: str) -> list[Topic]: ...


class LiteratureClient(Protocol):
    def summaries(self, pmids: list[str]) -> list[Article]: ...


class DiagnosisClient(Protocol):
    def resolve(self, term: str) -> tuple[str, str]: ...


@dataclass(frozen=True)
class AnalyteCode:
    analito_id: str
    loinc: str


@dataclass(frozen=True)
class ContentDraft:
    analito_id: str
    loinc: str
    title: str
    url: str


@dataclass(frozen=True)
class SourceRow:
    codigo: str
    nombre: str
    organizacion: str
    url: str
    identificador: str


@dataclass(frozen=True)
class RuleSource:
    regla_codigo: str
    source_codigo: str


@dataclass(frozen=True)
class DiagnosisCode:
    regla_codigo: str
    code: str
    title: str


@dataclass(frozen=True)
class ImportPlan:
    analyte_codes: tuple[AnalyteCode, ...] = ()
    contents: tuple[ContentDraft, ...] = ()
    sources: tuple[SourceRow, ...] = ()
    rule_sources: tuple[RuleSource, ...] = ()
    diagnoses: tuple[DiagnosisCode, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_plan(
    config: dict[str, Any],
    *,
    loinc: TerminologyClient,
    medlineplus: EducationClient,
    pubmed: LiteratureClient,
    icd11: DiagnosisClient,
) -> ImportPlan:
    """Consult every source for the configured items. Raises ExternalSourceError on failure."""
    analyte_codes: list[AnalyteCode] = []
    contents: list[ContentDraft] = []
    notes: list[str] = []
    for item in config.get("analytes", []):
        display = loinc.lookup(item["loinc"], item["expected_display"])
        analyte_codes.append(AnalyteCode(item["analito_id"], item["loinc"]))
        if item.get("medlineplus"):
            for topic in medlineplus.topics(item["loinc"]):
                contents.append(
                    ContentDraft(item["analito_id"], item["loinc"], topic.title, topic.url)
                )
        notes.append(f"LOINC {item['loinc']} confirmed: {display}")

    sources: list[SourceRow] = []
    rule_sources: list[RuleSource] = []
    diagnoses: list[DiagnosisCode] = []
    for rule in config.get("rules", []):
        pmids = list(rule.get("pubmed_pmids", []))
        for article in pubmed.summaries(pmids):
            sources.append(
                SourceRow(
                    codigo=f"PMID-{article.pmid}",
                    nombre=article.title[:200],
                    organizacion=article.journal[:200],
                    url=article.url,
                    identificador=article.pmid,
                )
            )
            rule_sources.append(RuleSource(rule["regla_codigo"], f"PMID-{article.pmid}"))
        term = rule.get("icd_search")
        if term:
            code, title = icd11.resolve(term)
            diagnoses.append(DiagnosisCode(rule["regla_codigo"], code, title))
            notes.append(f"ICD-11 {code} proposed for {rule['regla_codigo']}")

    if contents:
        sources.append(
            SourceRow(
                codigo=MEDLINEPLUS_SOURCE,
                nombre="MedlinePlus Connect",
                organizacion="U.S. National Library of Medicine",
                url="https://medlineplus.gov/",
                identificador="MedlinePlus Connect",
            )
        )
    return ImportPlan(
        analyte_codes=tuple(analyte_codes),
        contents=tuple(contents),
        sources=tuple(_unique_sources(sources)),
        rule_sources=tuple(rule_sources),
        diagnoses=tuple(diagnoses),
        notes=tuple(notes),
    )


def _unique_sources(sources: list[SourceRow]) -> list[SourceRow]:
    seen: dict[str, SourceRow] = {}
    for row in sources:
        seen.setdefault(row.codigo, row)
    return list(seen.values())


__all__ = [
    "AnalyteCode",
    "ContentDraft",
    "DiagnosisCode",
    "ExternalSourceError",
    "ImportPlan",
    "RuleSource",
    "SourceRow",
    "build_plan",
]

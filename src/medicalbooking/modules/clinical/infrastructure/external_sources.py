"""Read-only clients for the external knowledge sources: LOINC, MedlinePlus, PubMed, ICD-11.

Standard library only, so no new runtime dependency. Errors carry the source name and a
failure kind, never a response body, a URL with credentials, or a secret.

What each source contributes to the knowledge base:
- LOINC: terminology. Scale (quantitative, ordinal, nominal, document), specimen (blood, serum,
  urine), example unit, class and a Spanish name. It never gives reference ranges.
- MedlinePlus Connect: educational summaries in Spanish for a LOINC code.
- PubMed: evidence identity and quality hints (publication type, MeSH terms, abstract). It does not
  give ranges either: a reviewer reads the abstract and records the range with its PMID.
- ICD-11 (WHO): the code, the class of the entity and its official page.
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

TIMEOUT_SECONDS = 20
ICD_OAUTH_ENDPOINT = "https://icdaccessmanagement.who.int/connect/token"
ICD_RELEASE = "https://id.who.int/icd/release/11/2024-01/mms/"
LOINC_LOOKUP_URL = "https://fhir.loinc.org/CodeSystem/$lookup?system=http://loinc.org&code="
MEDLINEPLUS_URL = (
    "https://connect.medlineplus.gov/service?mainSearchCriteria.v.cs=2.16.840.1.113883.6.1"
    "&mainSearchCriteria.v.c={code}&knowledgeResponseType=application/json"
    "&informationRecipient.languageCode.c=es"
)
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


class ExternalSourceError(Exception):
    """A source failed or answered something unexpected. Nothing from the response is kept."""

    def __init__(self, source: str, kind: str) -> None:
        super().__init__(f"{source}: {kind}")
        self.source = source
        self.kind = kind


@dataclass(frozen=True)
class Topic:
    title: str
    url: str
    summary: str = ""


@dataclass(frozen=True)
class Article:
    pmid: str
    title: str
    journal: str
    url: str
    year: str = ""
    publication_types: tuple[str, ...] = ()
    mesh_terms: tuple[str, ...] = ()
    abstract: str = ""


@dataclass(frozen=True)
class LoincProfile:
    code: str
    display: str
    spanish_name: str
    scale: str
    specimen: str
    example_unit: str
    loinc_class: str
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IcdEntity:
    code: str
    class_kind: str
    browser_url: str


def _strip_markup(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


def _https_only(source: str, url: str) -> None:
    if not url.startswith("https://"):
        raise ExternalSourceError(source, "insecure_url")


def _request(
    source: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> bytes:
    _https_only(source, url)
    request = urllib.request.Request(url, data=body, headers=headers or {})  # noqa: S310
    try:
        # https is enforced above and every URL is a constant of this module.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as error:
        raise ExternalSourceError(source, f"http_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ExternalSourceError(source, "unreachable") from None


def _request_json(
    source: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> Any:
    raw = _request(source, url, headers, body)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ExternalSourceError(source, "invalid_json") from None


def _part(parts: list[dict[str, Any]], name: str) -> str:
    """Value of a named part in a FHIR parameter: valueCode, valueString or valueCoding.code."""
    for part in parts:
        if part.get("name") == name:
            return (
                part.get("valueCode")
                or part.get("valueString")
                or (part.get("valueCoding") or {}).get("code", "")
            )
    return ""


class Loinc:
    """Terminology: confirms a code is active and describes it for the catalog."""

    def __init__(self, username: str, password: str) -> None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/fhir+json"}
        self._parts: dict[str, str] = {}

    def _lookup(self, code: str) -> list[dict[str, Any]]:
        data = _request_json("LOINC", LOINC_LOOKUP_URL + urllib.parse.quote(code), self._headers)
        return list(data.get("parameter", []))

    def _named(self, parameters: list[dict[str, Any]], name: str) -> dict[str, Any]:
        return next((p for p in parameters if p.get("name") == name), {})

    def _part_name(self, code: str) -> str:
        if code not in self._parts:
            display = self._named(self._lookup(code), "display").get("valueString", "")
            self._parts[code] = display or code
        return self._parts[code]

    def _checked(self, code: str, expected_display: str) -> list[dict[str, Any]]:
        parameters = self._lookup(code)
        display = self._named(parameters, "display").get("valueString", "")
        status = self._named(parameters, "status").get("valueCode")
        if status != "active" or expected_display.lower() not in display.lower():
            raise ExternalSourceError("LOINC", "code_mismatch")
        return parameters

    def lookup(self, code: str, expected_display: str) -> str:
        parameters = self._checked(code, expected_display)
        return self._named(parameters, "display").get("valueString", "")

    def profile(self, code: str, expected_display: str) -> LoincProfile:
        parameters = self._checked(code, expected_display)
        display = self._named(parameters, "display").get("valueString", "")
        properties: dict[str, str] = {}
        for item in parameters:
            if item.get("name") == "property":
                parts = item.get("part", [])
                key = _part(parts, "code")
                if key:
                    properties[key] = _part(parts, "value")
        spanish = ""
        for item in parameters:
            if item.get("name") != "designation":
                continue
            parts = item.get("part", [])
            if _part(parts, "language") == "es-ES" and _part(parts, "use") == "FullySpecifiedName":
                spanish = _part(parts, "value")
                break
        return LoincProfile(
            code=code,
            display=display,
            spanish_name=spanish,
            scale=self._part_name(properties["SCALE_TYP"]) if properties.get("SCALE_TYP") else "",
            specimen=self._part_name(properties["SYSTEM"]) if properties.get("SYSTEM") else "",
            example_unit=properties.get("EXAMPLE_UCUM_UNITS", ""),
            loinc_class=self._part_name(properties["CLASS"]) if properties.get("CLASS") else "",
        )


class MedlinePlus:
    """Educational topics in Spanish for a LOINC code. No key required."""

    def topics(self, loinc_code: str) -> list[Topic]:
        data = _request_json(
            "MedlinePlus", MEDLINEPLUS_URL.format(code=urllib.parse.quote(loinc_code))
        )
        topics = []
        for entry in data.get("feed", {}).get("entry", []):
            title = entry.get("title", {}).get("_value")
            links = entry.get("link", [])
            href = links[0].get("href") if links else None
            summary = _strip_markup(entry.get("summary", {}).get("_value", ""))
            if title and href:
                topics.append(Topic(title=title, url=href.split("?")[0], summary=summary[:2000]))
        return topics


class PubMed:
    """Evidence identity, publication type, MeSH terms and abstract. No key required."""

    def search(self, term: str, retmax: int = 5) -> list[str]:
        query = urllib.parse.urlencode(
            {"db": "pubmed", "retmode": "json", "retmax": retmax, "sort": "relevance", "term": term}
        )
        data = _request_json("PubMed", PUBMED_BASE + "esearch.fcgi?" + query)
        return list(data.get("esearchresult", {}).get("idlist", []))

    def details(self, pmids: list[str]) -> list[Article]:
        if not pmids:
            return []
        query = urllib.parse.urlencode({"db": "pubmed", "retmode": "xml", "id": ",".join(pmids)})
        raw = _request("PubMed", PUBMED_BASE + "efetch.fcgi?" + query)
        try:
            root = ET.fromstring(raw)  # noqa: S314 - NCBI response over HTTPS, parsed read-only
        except ET.ParseError:
            raise ExternalSourceError("PubMed", "invalid_xml") from None
        articles = []
        for node in root.findall(".//PubmedArticle"):
            pmid = node.findtext(".//PMID", default="")
            if not pmid:
                continue
            citation = node.find("MedlineCitation")
            article = citation.find("Article") if citation is not None else None
            if article is None:
                raise ExternalSourceError("PubMed", "not_found")
            abstract = " ".join(
                "".join(part.itertext()).strip()
                for part in article.findall("Abstract/AbstractText")
            )
            articles.append(
                Article(
                    pmid=pmid,
                    title=_strip_markup("".join(article.find("ArticleTitle").itertext()))
                    if article.find("ArticleTitle") is not None
                    else "",
                    journal=article.findtext("Journal/Title", default=""),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    year=article.findtext("Journal/JournalIssue/PubDate/Year", default=""),
                    publication_types=tuple(
                        item.text or ""
                        for item in article.findall("PublicationTypeList/PublicationType")
                    ),
                    mesh_terms=tuple(
                        item.findtext("DescriptorName", default="")
                        for item in citation.findall("MeshHeadingList/MeshHeading")
                    )[:12],
                    abstract=abstract[:4000],
                )
            )
        if len(articles) != len(pmids):
            raise ExternalSourceError("PubMed", "not_found")
        return articles


class Icd11:
    """WHO ICD-11: OAuth 2.0 client credentials, a search, and the official entity page."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_value: str | None = None

    def _token(self) -> str:
        if self._token_value is None:
            body = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "scope": "icdapi_access",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
            ).encode("utf-8")
            data = _request_json(
                "ICD-11",
                ICD_OAUTH_ENDPOINT,
                {"Content-Type": "application/x-www-form-urlencoded"},
                body,
            )
            token = data.get("access_token")
            if not token:
                raise ExternalSourceError("ICD-11", "no_token")
            self._token_value = token
        return self._token_value

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
            "Accept-Language": "es",
            "API-Version": "v2",
        }

    def resolve(self, term: str) -> tuple[str, str, str]:
        """Return (code, title, entity_id) of the first search hit that has a code."""
        data = _request_json(
            "ICD-11", ICD_RELEASE + "search?q=" + urllib.parse.quote(term), self._headers()
        )
        for entity in data.get("destinationEntities", []):
            code = entity.get("theCode")
            if code:
                entity_id = (entity.get("id") or "").replace("http://", "https://", 1)
                return code, _strip_markup(entity.get("title", "")), entity_id
        raise ExternalSourceError("ICD-11", "no_code")

    def entity(self, entity_id: str) -> IcdEntity:
        """Official page of an entity. The search returns http:// identifiers, which drop the
        token on redirect, so the identifier is always requested over https."""
        _https_only("ICD-11", entity_id)
        data = _request_json("ICD-11", entity_id, self._headers())
        return IcdEntity(
            code=data.get("code", ""),
            class_kind=data.get("classKind", ""),
            browser_url=data.get("browserUrl", ""),
        )

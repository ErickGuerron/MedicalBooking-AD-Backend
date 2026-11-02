"""Read-only clients for the external knowledge sources: LOINC, MedlinePlus, PubMed, ICD-11.

Standard library only, so no new runtime dependency. Errors carry the source name and a
failure kind, never a response body, a URL with credentials, or a secret.
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TIMEOUT_SECONDS = 20
ICD_OAUTH_ENDPOINT = "https://icdaccessmanagement.who.int/connect/token"
ICD_SEARCH_URL = "https://id.who.int/icd/release/11/2024-01/mms/search?q="
LOINC_LOOKUP_URL = "https://fhir.loinc.org/CodeSystem/$lookup?system=http://loinc.org&code="
MEDLINEPLUS_URL = (
    "https://connect.medlineplus.gov/service?mainSearchCriteria.v.cs=2.16.840.1.113883.6.1"
    "&mainSearchCriteria.v.c={code}&knowledgeResponseType=application/json"
    "&informationRecipient.languageCode.c=es"
)
PUBMED_SUMMARY_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id="
)


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


@dataclass(frozen=True)
class Article:
    pmid: str
    title: str
    journal: str
    url: str


def _strip_markup(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


def _request_json(
    source: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> Any:
    if not url.startswith("https://"):
        raise ExternalSourceError(source, "insecure_url")
    request = urllib.request.Request(url, data=body, headers=headers or {})  # noqa: S310
    try:
        # https is enforced above and every URL is a constant of this module.
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ExternalSourceError(source, f"http_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ExternalSourceError(source, "unreachable") from None
    except ValueError:
        raise ExternalSourceError(source, "invalid_json") from None


class Loinc:
    """Terminology only: confirms that a code is active and names what we expect."""

    def __init__(self, username: str, password: str) -> None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/fhir+json"}

    def lookup(self, code: str, expected_display: str) -> str:
        data = _request_json("LOINC", LOINC_LOOKUP_URL + urllib.parse.quote(code), self._headers)
        parameters = {item.get("name"): item for item in data.get("parameter", [])}
        display = parameters.get("display", {}).get("valueString", "")
        status = parameters.get("status", {}).get("valueCode")
        if status != "active" or expected_display.lower() not in display.lower():
            raise ExternalSourceError("LOINC", "code_mismatch")
        return display


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
            if title and href:
                topics.append(Topic(title=title, url=href.split("?")[0]))
        return topics


class PubMed:
    """Bibliographic identity of evidence (PMID, title, journal). No key required."""

    def summaries(self, pmids: list[str]) -> list[Article]:
        if not pmids:
            return []
        data = _request_json("PubMed", PUBMED_SUMMARY_URL + ",".join(pmids))
        result = data.get("result", {})
        articles = []
        for pmid in pmids:
            item = result.get(pmid)
            if not item or "error" in item:
                raise ExternalSourceError("PubMed", "not_found")
            articles.append(
                Article(
                    pmid=pmid,
                    title=_strip_markup(item.get("title", "")),
                    journal=item.get("fulljournalname") or item.get("source", ""),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                )
            )
        return articles


class Icd11:
    """WHO ICD-11: OAuth 2.0 client credentials, then a search that returns MMS codes."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

    def _token(self) -> str:
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
        return token

    def resolve(self, term: str) -> tuple[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
            "Accept-Language": "es",
            "API-Version": "v2",
        }
        data = _request_json("ICD-11", ICD_SEARCH_URL + urllib.parse.quote(term), headers)
        for entity in data.get("destinationEntities", []):
            code = entity.get("theCode")
            if code:
                return code, _strip_markup(entity.get("title", ""))
        raise ExternalSourceError("ICD-11", "no_code")

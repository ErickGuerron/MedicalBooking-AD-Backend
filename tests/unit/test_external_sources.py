"""External source clients: parsing and failure handling, with recorded responses (no network)."""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.infrastructure import external_sources as sources
from medicalbooking.modules.clinical.infrastructure.external_sources import (
    ExternalSourceError,
    Icd11,
    Loinc,
    MedlinePlus,
    PubMed,
)


def _replay(monkeypatch, response):
    """Make the shared HTTP helper return a recorded response (or raise a recorded error)."""

    def fake(source, url, headers=None, body=None):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(sources, "_request_json", fake)


def test_loinc_accepts_an_active_code_with_the_expected_name(monkeypatch) -> None:
    _replay(
        monkeypatch,
        {
            "parameter": [
                {"name": "display", "valueString": "Hemoglobin [Mass/volume] in Blood"},
                {"name": "status", "valueCode": "active"},
            ]
        },
    )

    assert "Hemoglobin" in Loinc("u", "p").lookup("718-7", "Hemoglobin")


def test_loinc_refuses_a_code_whose_name_does_not_match(monkeypatch) -> None:
    _replay(
        monkeypatch,
        {
            "parameter": [
                {"name": "display", "valueString": "Platelet count"},
                {"name": "status", "valueCode": "active"},
            ]
        },
    )

    with pytest.raises(ExternalSourceError) as error:
        Loinc("u", "p").lookup("718-7", "Hemoglobin")
    assert error.value.kind == "code_mismatch"


def test_loinc_refuses_a_retired_code(monkeypatch) -> None:
    _replay(
        monkeypatch,
        {
            "parameter": [
                {"name": "display", "valueString": "Hemoglobin"},
                {"name": "status", "valueCode": "deprecated"},
            ]
        },
    )

    with pytest.raises(ExternalSourceError):
        Loinc("u", "p").lookup("718-7", "Hemoglobin")


def test_medlineplus_returns_titles_and_links_without_tracking_parameters(monkeypatch) -> None:
    _replay(
        monkeypatch,
        {
            "feed": {
                "entry": [
                    {
                        "title": {"_value": "Glucosa en la sangre"},
                        "link": [
                            {
                                "href": "https://medlineplus.gov/spanish/bloodglucose.html?utm_source=x"
                            }
                        ],
                    },
                    {"title": {"_value": "Sin enlace"}, "link": []},
                ]
            }
        },
    )

    topics = MedlinePlus().topics("2339-0")

    assert [t.title for t in topics] == ["Glucosa en la sangre"]
    assert topics[0].url == "https://medlineplus.gov/spanish/bloodglucose.html"


PUBMED_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>42969393</PMID>
      <Article>
        <Journal>
          <Title>Clinical laboratory</Title>
          <JournalIssue><PubDate><Year>2015</Year></PubDate></JournalIssue>
        </Journal>
        <ArticleTitle>Reference <i>intervals</i> for hemoglobin</ArticleTitle>
        <Abstract><AbstractText>Our study aimed to establish the range.</AbstractText></Abstract>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList><MeshHeading><DescriptorName>Adult</DescriptorName></MeshHeading></MeshHeadingList>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_pubmed_parses_title_year_type_mesh_and_abstract(monkeypatch) -> None:
    monkeypatch.setattr(
        sources, "_request", lambda source, url, headers=None, body=None: PUBMED_XML
    )

    article = PubMed().details(["42969393"])[0]

    assert article.title == "Reference intervals for hemoglobin"
    assert article.year == "2015"
    assert article.publication_types == ("Journal Article",)
    assert article.mesh_terms == ("Adult",)
    assert "establish the range" in article.abstract
    assert article.url == "https://pubmed.ncbi.nlm.nih.gov/42969393/"


def test_pubmed_refuses_a_pmid_it_does_not_return(monkeypatch) -> None:
    monkeypatch.setattr(
        sources, "_request", lambda source, url, headers=None, body=None: PUBMED_XML
    )

    with pytest.raises(ExternalSourceError) as error:
        PubMed().details(["42969393", "99999999"])
    assert error.value.kind == "not_found"


def test_icd11_returns_the_first_code_with_markup_removed(monkeypatch) -> None:
    client = Icd11("id", "secret")
    monkeypatch.setattr(client, "_token", lambda: "t")
    _replay(
        monkeypatch,
        {
            "destinationEntities": [
                {"theCode": "3A9Z", "title": "<em class='found'>Anemia</em>, sin especificación"}
            ]
        },
    )

    assert client.resolve("anemia") == ("3A9Z", "Anemia, sin especificación", "")


def test_icd11_without_token_fails_closed(monkeypatch) -> None:
    _replay(monkeypatch, {"token_type": "Bearer"})

    with pytest.raises(ExternalSourceError) as error:
        Icd11("id", "secret")._token()
    assert error.value.kind == "no_token"


def test_failures_never_carry_the_response_or_the_secret(monkeypatch) -> None:
    error = ExternalSourceError("ICD-11", "http_401")

    assert "secret" not in str(error)
    assert str(error) == "ICD-11: http_401"

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


def test_pubmed_refuses_an_unknown_pmid(monkeypatch) -> None:
    _replay(monkeypatch, {"result": {"uids": [], "999": {"error": "cannot get document summary"}}})

    with pytest.raises(ExternalSourceError) as error:
        PubMed().summaries(["999"])
    assert error.value.kind == "not_found"


def test_pubmed_strips_markup_from_titles(monkeypatch) -> None:
    _replay(
        monkeypatch,
        {
            "result": {
                "42969393": {
                    "title": "Hemoglobin <i>reference</i> ranges",
                    "fulljournalname": "Journal of Test",
                }
            }
        },
    )

    article = PubMed().summaries(["42969393"])[0]

    assert article.title == "Hemoglobin reference ranges"
    assert article.url == "https://pubmed.ncbi.nlm.nih.gov/42969393/"


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

    assert client.resolve("anemia") == ("3A9Z", "Anemia, sin especificación")


def test_icd11_without_token_fails_closed(monkeypatch) -> None:
    _replay(monkeypatch, {"token_type": "Bearer"})

    with pytest.raises(ExternalSourceError) as error:
        Icd11("id", "secret")._token()
    assert error.value.kind == "no_token"


def test_failures_never_carry_the_response_or_the_secret(monkeypatch) -> None:
    error = ExternalSourceError("ICD-11", "http_401")

    assert "secret" not in str(error)
    assert str(error) == "ICD-11: http_401"

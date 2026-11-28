# ruff: noqa: F811  (pytest injects the fixtures imported below by parameter name)
"""Catalogs, date-range reads, the event log and the patient's own history, against SQL Server."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest  # noqa: F401  (fixtures come from the history API module)
from sqlalchemy import text

from tests.integration.test_history_api import (  # noqa: F401  (pytest fixtures)
    ALLERGY,
    _lab,
    _stranger_doctor,
    _treated_patient,
    app,
    engine,
)

FORBIDDEN = "Access to this resource is not allowed"


# ---------- catalogs ----------


def test_the_catalogs_are_searchable_by_any_signed_in_person(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    vaccines = client.get("/api/v1/catalogos/vacunas?q=influenza", headers=ctx["patient"])
    assert vaccines.status_code == 200
    assert [item["codigo"] for item in vaccines.get_json()["items"]] == ["INFLUENZA"]
    allergens = client.get("/api/v1/catalogos/sustancias-alergia?q=peni", headers=ctx["doctor"])
    assert allergens.get_json()["items"][0] == {
        "codigo": "PENICILINA",
        "nombre": "Penicilina",
        "categoria": "MEDICAMENTO",
    }
    assert (
        client.get("/api/v1/catalogos/cie11?q=hipertension", headers=_lab(app, engine)).status_code
        == 200
    )


def test_a_catalog_search_treats_wildcards_as_text(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().get("/api/v1/catalogos/vacunas?q=%25", headers=ctx["doctor"])
    assert response.status_code == 200
    assert response.get_json()["items"] == []


def test_an_unknown_catalog_or_a_bad_limit_is_a_422(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    assert client.get("/api/v1/catalogos/tabla_secreta", headers=ctx["doctor"]).status_code == 422
    assert client.get("/api/v1/catalogos/vacunas?limit=0", headers=ctx["doctor"]).status_code == 422
    assert client.get("/api/v1/catalogos/vacunas", headers={}).status_code == 401


def test_a_catalog_code_must_exist_at_the_database_too(app, engine):
    """The foreign key is the last line of defense: a code the service never checked is refused."""
    ctx = _treated_patient(app, engine)
    with pytest.raises(Exception, match="FK_vacunas_catalogo|foreign key|REFERENCE"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO historia_vacunas (id, paciente_id, vacuna, dosis_numero, fecha, "
                    "origen, "
                    "verificada, verificada_por, verificada_en, registrada_por, vacuna_codigo) "
                    "VALUES (NEWID(), :p, 'x', 1, '2026-01-01', 'REGISTRADA_MEDICO', 1, :u, "
                    "SYSUTCDATETIME(), :u, 'NO_EXISTE')"
                ),
                {"p": ctx["paciente_id"], "u": ctx["doctor_user"]},
            )


# ---------- date ranges ----------


def test_the_history_read_filters_by_the_requested_days(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    url = f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias"
    client.post(url, json=ALLERGY, headers=ctx["doctor"])
    today = datetime.now(UTC).date()
    inside = client.get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia?desde={today}&hasta={today}",
        headers=ctx["doctor"],
    ).get_json()
    outside = client.get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia?desde={today + timedelta(days=1)}",
        headers=ctx["doctor"],
    ).get_json()
    assert len(inside["alergias"]) == 1
    assert outside["alergias"] == []


def test_a_range_that_is_malformed_or_too_wide_is_a_422(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia"
    assert client.get(f"{base}?desde=ayer", headers=ctx["doctor"]).status_code == 422
    assert (
        client.get(f"{base}?desde=2026-06-01&hasta=2026-01-01", headers=ctx["doctor"]).status_code
        == 422
    )
    assert (
        client.get(f"{base}?desde=2024-01-01&hasta=2026-01-01", headers=ctx["doctor"]).status_code
        == 422
    )


def test_a_range_that_matches_nothing_gives_empty_lists_not_an_error(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia?desde=1990-01-01&hasta=1990-01-31",
        headers=ctx["doctor"],
    )
    assert response.status_code == 200
    assert response.get_json()["alergias"] == []


# ---------- the event log ----------


def test_the_event_log_pages_newest_first_and_hides_the_actor_from_the_patient(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia"
    entry = client.post(f"{base}/alergias", json=ALLERGY, headers=ctx["doctor"]).get_json()
    client.patch(
        f"{base}/alergias/{entry['id']}/estado",
        json={"expected_estado": "ACTIVA", "estado": "RESUELTA", "motivo": "ok"},
        headers=ctx["doctor"],
    )

    doctor_page = client.get(f"{base}/eventos?limit=1", headers=ctx["doctor"]).get_json()
    assert doctor_page["total"] == 2
    assert doctor_page["items"][0]["accion"] == "ESTADO"
    assert "actor_usuario_id" in doctor_page["items"][0]

    patient_page = client.get(f"{base}/eventos", headers=ctx["patient"]).get_json()
    assert patient_page["incluye_actor"] is False
    assert all("actor_usuario_id" not in item for item in patient_page["items"])
    assert patient_page["items"][0]["motivo"] == "ok"


def test_the_event_log_filters_by_entity_and_action(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    base = f"/api/v1/patients/{ctx['paciente_id']}/historia"
    client.post(f"{base}/alergias", json=ALLERGY, headers=ctx["doctor"])
    only_allergies = client.get(f"{base}/eventos?entidad=alergia", headers=ctx["doctor"]).get_json()
    assert only_allergies["total"] == 1
    assert client.get(f"{base}/eventos?entidad=tabla", headers=ctx["doctor"]).status_code == 422


def test_the_event_log_is_closed_to_strangers_and_is_audited(app, engine):
    ctx = _treated_patient(app, engine)
    response = app.test_client().get(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/eventos",
        headers=_stranger_doctor(app, engine),
    )
    assert response.status_code == 403
    assert response.get_json()["detail"] == FORBIDDEN
    with engine.connect() as conn:
        denied = conn.execute(
            text(
                "SELECT COUNT(*) FROM auditoria_accesos WHERE paciente_id = :p "
                "AND accion = 'consultar_eventos' "
                "AND resultado = 'DENEGADO'"
            ),
            {"p": ctx["paciente_id"]},
        ).scalar()
    assert denied >= 1


# ---------- the patient's own history ----------


def test_the_patient_reads_their_own_history_and_events_through_me(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    client.post(
        f"/api/v1/patients/{ctx['paciente_id']}/historia/alergias",
        json=ALLERGY,
        headers=ctx["doctor"],
    )
    own = client.get("/api/v1/me/historia", headers=ctx["patient"])
    assert own.status_code == 200
    assert own.get_json()["paciente_id"] == ctx["paciente_id"]
    assert len(own.get_json()["alergias"]) == 1
    events = client.get("/api/v1/me/historia/eventos", headers=ctx["patient"]).get_json()
    assert events["total"] >= 1


def test_me_is_refused_to_a_doctor_and_to_the_laboratory(app, engine):
    ctx = _treated_patient(app, engine)
    client = app.test_client()
    assert client.get("/api/v1/me/historia", headers=ctx["doctor"]).status_code == 403
    assert client.get("/api/v1/me/historia/eventos", headers=_lab(app, engine)).status_code == 403

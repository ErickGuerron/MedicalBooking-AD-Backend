"""Clinical history rules: states, verification, text limits, codes, vaccines and vital ranges."""

from __future__ import annotations

from datetime import date

import pytest

from medicalbooking.modules.history.domain.policies import (
    ALERGIA,
    ANTECEDENTE,
    MEDICACION,
    PROBLEMA,
    SUSTANCIA_MAX,
    VACUNA,
    can_verify,
    origin_and_verification,
    validate_alergia,
    validate_antecedente,
    validate_cie11,
    validate_medicacion,
    validate_motivo,
    validate_problema,
    validate_transition,
    validate_vacuna,
    validate_vitales,
)
from medicalbooking.shared.domain.errors import Conflict, ValidationFailed

TODAY = date(2026, 11, 25)


# ---------- transitions ----------


def test_an_active_allergy_can_be_resolved_with_a_motivo() -> None:
    validate_transition(ALERGIA, "ACTIVA", "RESUELTA", "Desensibilizada en 2025")


def test_a_resolved_allergy_can_come_back_active_with_a_motivo() -> None:
    validate_transition(ALERGIA, "RESUELTA", "ACTIVA", "Reaccion nueva")


@pytest.mark.parametrize("motivo", [None, "", "   ", "\t\n"])
def test_every_state_change_needs_a_motivo(motivo: str | None) -> None:
    with pytest.raises(ValidationFailed):
        validate_transition(ALERGIA, "ACTIVA", "RESUELTA", motivo)


def test_a_motivo_longer_than_500_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        validate_motivo("m" * 501)


def test_the_erroneous_state_is_final_for_every_kind() -> None:
    for kind, final in [
        (ALERGIA, "ERRONEA"),
        (ANTECEDENTE, "ERRONEO"),
        (MEDICACION, "ERRONEA"),
        (PROBLEMA, "ERRONEO"),
        (VACUNA, "ERRONEA"),
    ]:
        with pytest.raises(Conflict):
            validate_transition(kind, final, next(iter(kind.states - {final})), "motivo")


def test_an_undeclared_transition_is_refused_as_a_conflict() -> None:
    # A vaccine can only be marked erroneous; it cannot be reactivated.
    with pytest.raises(Conflict):
        validate_transition(VACUNA, "ERRONEA", "ACTIVA", "motivo")
    with pytest.raises(Conflict):
        validate_transition(MEDICACION, "SUSPENDIDA", "SUSPENDIDA", "motivo")


def test_an_unknown_target_state_is_a_validation_error_not_a_conflict() -> None:
    with pytest.raises(ValidationFailed):
        validate_transition(ALERGIA, "ACTIVA", "BORRADA", "motivo")


def test_medication_can_be_suspended_and_resumed() -> None:
    validate_transition(MEDICACION, "ACTIVA", "SUSPENDIDA", "Efecto adverso")
    validate_transition(MEDICACION, "SUSPENDIDA", "ACTIVA", "Se reinicia")


# ---------- verification ----------


def test_a_doctor_entry_is_verified_at_once() -> None:
    assert origin_and_verification(is_doctor=True) == ("REGISTRADA_MEDICO", True)


def test_a_patient_declaration_starts_unverified() -> None:
    assert origin_and_verification(is_doctor=False) == ("DECLARADA_PACIENTE", False)


def test_a_declared_entry_can_be_verified_once() -> None:
    can_verify("DECLARADA_PACIENTE", verificada=False)
    with pytest.raises(Conflict):
        can_verify("DECLARADA_PACIENTE", verificada=True)


def test_a_doctor_entry_never_needs_verifying() -> None:
    with pytest.raises(Conflict):
        can_verify("REGISTRADA_MEDICO", verificada=False)


# ---------- allergies ----------


def test_an_allergy_needs_a_known_category_and_severity() -> None:
    with pytest.raises(ValidationFailed):
        validate_alergia(
            sustancia="Penicilina", categoria="PLANTA", severidad="LEVE", reaccion=None
        )
    with pytest.raises(ValidationFailed):
        validate_alergia(
            sustancia="Penicilina", categoria="MEDICAMENTO", severidad="MUY_GRAVE", reaccion=None
        )


def test_an_allergy_substance_is_trimmed_and_limited() -> None:
    fields = validate_alergia(
        sustancia="  Penicilina  ", categoria="MEDICAMENTO", severidad="GRAVE", reaccion=""
    )
    assert fields["sustancia"] == "Penicilina"
    assert fields["reaccion"] is None
    with pytest.raises(ValidationFailed):
        validate_alergia(
            sustancia="x" * (SUSTANCIA_MAX + 1), categoria="OTRA", severidad="LEVE", reaccion=None
        )


def test_an_allergy_substance_cannot_be_blank() -> None:
    with pytest.raises(ValidationFailed):
        validate_alergia(sustancia="   ", categoria="OTRA", severidad="LEVE", reaccion=None)


# ---------- antecedents ----------


def test_a_family_antecedent_needs_the_relative() -> None:
    with pytest.raises(ValidationFailed):
        validate_antecedente(tipo="FAMILIAR", descripcion="Diabetes", parentesco=None, cie11=None)


def test_a_personal_antecedent_drops_any_relative_it_was_sent() -> None:
    fields = validate_antecedente(
        tipo="PERSONAL", descripcion="Asma", parentesco="Madre", cie11=None
    )
    assert fields["parentesco"] is None


def test_an_unknown_antecedent_type_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        validate_antecedente(tipo="SOCIAL", descripcion="x", parentesco=None, cie11=None)


# ---------- CIE-11 codes ----------


@pytest.mark.parametrize("code", ["BA00", "5A11", "5A11.0", "ba00", "CA40.Z"])
def test_valid_cie11_codes_are_accepted_and_upper_cased(code: str) -> None:
    assert validate_cie11(code) == code.upper()


@pytest.mark.parametrize(
    "code", ["BA 00", "BA00;DROP", "<script>", "ABCDEFGH", "BA00..1", ".BA00", "ñ12"]
)
def test_malformed_cie11_codes_are_refused(code: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_cie11(code)


def test_an_empty_cie11_is_stored_as_none() -> None:
    assert validate_cie11("   ") is None
    assert validate_cie11(None) is None


# ---------- medication and problems ----------


def test_medication_needs_a_name_and_keeps_optional_fields_empty() -> None:
    fields = validate_medicacion(medicamento="Losartan", dosis=" ", frecuencia=None, inicio=None)
    assert fields["dosis"] is None
    with pytest.raises(ValidationFailed):
        validate_medicacion(medicamento="  ", dosis=None, frecuencia=None, inicio=None)


def test_a_problem_keeps_its_cie11_when_given() -> None:
    fields = validate_problema(descripcion="Hipertension", cie11="BA00", inicio=None)
    assert fields["cie11"] == "BA00"


# ---------- vaccines ----------


def test_a_vaccine_dose_is_an_integer_from_1_to_10() -> None:
    for valid in (1, 10):
        validate_vacuna(vacuna="Influenza", dosis_numero=valid, fecha=TODAY, lote=None, today=TODAY)
    for invalid in (0, 11, -1):
        with pytest.raises(ValidationFailed):
            validate_vacuna(
                vacuna="Influenza", dosis_numero=invalid, fecha=TODAY, lote=None, today=TODAY
            )


def test_a_boolean_is_not_a_vaccine_dose() -> None:
    with pytest.raises(ValidationFailed):
        validate_vacuna(vacuna="Influenza", dosis_numero=True, fecha=TODAY, lote=None, today=TODAY)


def test_a_vaccine_date_cannot_be_in_the_future_or_missing() -> None:
    with pytest.raises(ValidationFailed):
        validate_vacuna(
            vacuna="Influenza", dosis_numero=1, fecha=date(2026, 12, 1), lote=None, today=TODAY
        )
    with pytest.raises(ValidationFailed):
        validate_vacuna(vacuna="Influenza", dosis_numero=1, fecha=None, lote=None, today=TODAY)


# ---------- vital signs ----------


def test_one_measure_is_enough_and_the_rest_stay_empty() -> None:
    values = validate_vitales({"peso_kg": 70.5})
    assert values["peso_kg"] == 70.5
    assert values["talla_cm"] is None


def test_no_measure_at_all_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        validate_vitales({"peso_kg": None, "talla_cm": None})


@pytest.mark.parametrize(
    "key,value",
    [
        ("peso_kg", 0.4),
        ("peso_kg", 301),
        ("talla_cm", 29),
        ("pa_sistolica", 49),
        ("pa_diastolica", 161),
        ("frecuencia_cardiaca", 19),
        ("temperatura_c", 45.1),
        ("saturacion", 101),
    ],
)
def test_out_of_range_vitals_are_refused(key: str, value: float) -> None:
    with pytest.raises(ValidationFailed):
        validate_vitales({key: value})


def test_a_string_or_a_boolean_is_not_a_vital_measure() -> None:
    with pytest.raises(ValidationFailed):
        validate_vitales({"pa_sistolica": "120"})
    with pytest.raises(ValidationFailed):
        validate_vitales({"saturacion": True})


def test_an_unknown_vital_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        validate_vitales({"glucosa": 90})


def test_the_limits_themselves_are_accepted() -> None:
    validate_vitales({"peso_kg": 0.5, "talla_cm": 250, "pa_sistolica": 260, "saturacion": 50})
    validate_vitales({"temperatura_c": 45.0, "saturacion": 100, "pa_diastolica": 30})

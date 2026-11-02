"""Deterministic rule evaluation (E5): inputs, logic, evidence and proposal state. Pure domain."""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.evaluation import (
    CriterionSpec,
    ResultInput,
    RuleSpec,
    evaluate,
)

HB = "analito-hb"
GLU = "analito-glu"


def _result(rid, analito, valor, unidad, interpretacion) -> ResultInput:
    return ResultInput(
        resultado_id=rid,
        analito_id=analito,
        valor=None if valor is None else Decimal(valor),
        unidad=unidad,
        interpretacion=interpretacion,
    )


def _rule(code, criteria, logic="TODOS", tipo="HALLAZGO") -> RuleSpec:
    return RuleSpec(
        rule_id=f"id-{code}",
        codigo=code,
        version=1,
        tipo=tipo,
        expresion_logica=logic,
        mensaje=f"Mensaje {code}",
        criteria=tuple(criteria),
    )


LOW_HB = _rule("HB-BAJA", [CriterionSpec(HB, "LT", Decimal("12.0"), "g/dL")])
OUT_GLU = _rule("GLU-FUERA", [CriterionSpec(GLU, "FUERA_REFERENCIA", None, "mg/dL")])


def test_a_numeric_criterion_fires_with_its_evidence() -> None:
    results = [_result("r1", HB, "11.0", "g/dL", "BAJO")]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.estado == "PENDIENTE"
    assert [f.rule_code for f in evaluation.findings] == ["HB-BAJA"]
    assert evaluation.findings[0].rule_version == 1
    assert evaluation.findings[0].explicacion == "Mensaje HB-BAJA"
    assert evaluation.findings[0].evidence[0].resultado_id == "r1"
    assert evaluation.findings[0].evidence[0].valor == Decimal("11.0")


def test_a_normal_value_is_classified_but_fires_nothing() -> None:
    results = [_result("r1", HB, "14.0", "g/dL", "DENTRO_REFERENCIA")]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.findings == ()
    assert evaluation.estado == "PENDIENTE"


def test_nothing_classifiable_and_nothing_fired_is_sin_evidencia() -> None:
    results = [_result("r1", HB, None, None, "NO_EVALUABLE")]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.findings == ()
    assert evaluation.estado == "SIN_EVIDENCIA"


def test_missing_input_does_not_fire_a_todos_rule() -> None:
    results = [_result("r1", GLU, "120", "mg/dL", "ALTO")]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.findings == ()


def test_cualquiera_fires_on_one_satisfied_criterion_and_tolerates_a_missing_one() -> None:
    either = _rule(
        "HB-O-GLU",
        [
            CriterionSpec(HB, "LT", Decimal("12.0"), "g/dL"),
            CriterionSpec(GLU, "GT", Decimal("100"), "mg/dL"),
        ],
        logic="CUALQUIERA",
    )
    results = [_result("r1", GLU, "130", "mg/dL", "ALTO")]

    evaluation = evaluate(results, [either])

    assert [f.rule_code for f in evaluation.findings] == ["HB-O-GLU"]
    assert [e.resultado_id for e in evaluation.findings[0].evidence] == ["r1"]


def test_fuera_referencia_uses_the_stored_classification_only() -> None:
    fuera = _result("r1", GLU, "120", "mg/dL", "ALTO")
    dentro = _result("r2", GLU, "85", "mg/dL", "DENTRO_REFERENCIA")
    no_eval = _result("r3", GLU, None, "mg/dL", "NO_EVALUABLE")

    assert [f.rule_code for f in evaluate([fuera], [OUT_GLU]).findings] == ["GLU-FUERA"]
    assert evaluate([dentro], [OUT_GLU]).findings == ()
    assert evaluate([no_eval], [OUT_GLU]).findings == ()


def test_unit_mismatch_between_criterion_and_result_is_not_compared() -> None:
    results = [_result("r1", HB, "11.0", "mmol/L", "BAJO")]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.findings == ()


def test_two_results_for_one_analyte_are_ambiguous_and_not_used() -> None:
    results = [
        _result("r1", HB, "11.0", "g/dL", "BAJO"),
        _result("r2", HB, "13.0", "g/dL", "DENTRO_REFERENCIA"),
    ]

    evaluation = evaluate(results, [LOW_HB])

    assert evaluation.findings == ()


def test_findings_are_sorted_so_the_same_input_gives_the_same_output() -> None:
    results = [
        _result("r1", HB, "11.0", "g/dL", "BAJO"),
        _result("r2", GLU, "120", "mg/dL", "ALTO"),
    ]

    first = evaluate(results, [OUT_GLU, LOW_HB])
    second = evaluate(list(reversed(results)), [LOW_HB, OUT_GLU])

    assert [f.rule_code for f in first.findings] == ["GLU-FUERA", "HB-BAJA"]
    assert first == second


def test_summary_states_it_is_preliminary_not_a_diagnosis() -> None:
    evaluation = evaluate([_result("r1", HB, "11.0", "g/dL", "BAJO")], [LOW_HB])

    assert "no es un diagnostico" in evaluation.resumen.lower()


def test_two_criteria_on_one_analyte_record_one_evidence_per_result() -> None:
    rule = _rule(
        "HB-RANGO",
        [
            CriterionSpec(HB, "GE", Decimal("5.0"), "g/dL"),
            CriterionSpec(HB, "LE", Decimal("20.0"), "g/dL"),
        ],
    )

    evaluation = evaluate([_result("r1", HB, "10.0", "g/dL", None)], [rule])

    assert [item.resultado_id for item in evaluation.findings[0].evidence] == ["r1"]


def test_a_numeric_criterion_without_unit_never_compares_raw_numbers() -> None:
    no_unit = _rule("HB-SIN-UNIDAD", [CriterionSpec(HB, "LT", Decimal("12.0"), None)])
    results = [_result("r1", HB, "10", "g/L", "BAJO")]

    assert evaluate(results, [no_unit]).findings == ()

"""Snapshot items of the shadow findings (feature doc S9): values, sources and reasons, pure.

Every item keeps the source it was built from and an explicit reason when it is not evaluable (S3).
The snapshot never states that it changes a decision.
"""

from __future__ import annotations

from decimal import Decimal

from medicalbooking.modules.clinical.domain.shadow import ShadowInputs, shadow_report
from medicalbooking.modules.clinical.domain.snapshot import (
    ROLES_BY_FINDING,
    RULE_VERSION,
    SnapshotItem,
    snapshot_items,
)


def _report(**overrides):
    base = dict(
        sex="F",
        age_months=240,
        pregnancy=None,
        hemoglobin_g_dl=Decimal("10.0"),
        ferritin_ug_l=Decimal("8"),
        crp_mg_l=Decimal("2"),
        fasting_mg_dl=[Decimal("130")],
        hba1c_pct=[],
        ogtt_2h_mg_dl=[],
        random_mg_dl=None,
        symptoms=None,
    )
    base.update(overrides)
    return shadow_report(ShadowInputs(**base))


def test_items_come_in_fixed_order_one_per_finding() -> None:
    items = snapshot_items(_report())

    assert [item.hallazgo for item in items] == ["ANEMIA", "HIERRO", "DIABETES"]
    assert all(isinstance(item, SnapshotItem) for item in items)


def test_each_item_names_the_source_it_was_built_from() -> None:
    items = {item.hallazgo: item for item in snapshot_items(_report())}

    assert "WHO 2024" in items["ANEMIA"].fuente
    assert "WHO 2020" in items["HIERRO"].fuente
    assert "ADA 2026" in items["DIABETES"].fuente


def test_anaemia_item_keeps_the_cut_off_it_applied() -> None:
    anemia = {item.hallazgo: item for item in snapshot_items(_report(sex="M"))}["ANEMIA"]

    assert anemia.corte_valor is not None
    assert anemia.corte_unidad == "g/L"
    assert anemia.motivo is None


def test_missing_input_keeps_an_explicit_reason_instead_of_a_finding() -> None:
    items = {item.hallazgo: item for item in snapshot_items(_report(hemoglobin_g_dl=None))}

    assert items["ANEMIA"].categoria == "NO_EVALUABLE"
    assert items["ANEMIA"].motivo == "sin valor de hemoglobina"
    assert items["ANEMIA"].corte_valor is None


def test_rule_version_identifies_the_shadow_rules() -> None:
    assert RULE_VERSION == "sombra-hallazgos-2"


def test_roles_by_finding_name_exactly_the_results_each_finding_reads() -> None:
    assert ROLES_BY_FINDING == {
        "ANEMIA": ("hemoglobina",),
        "HIERRO": ("ferritina", "pcr"),
        "DIABETES": ("glucosa_ayuno", "hba1c"),
    }

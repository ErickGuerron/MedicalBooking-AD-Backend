"""Snapshot items of the shadow findings (feature doc S9). Pure functions.

A snapshot keeps, for each of the three findings, the category, the cut-off applied, the source the
cut-off comes from, and the reason when the finding is not evaluable (S3). Values and result ids
are stored with the snapshot by the application layer, from the evidence of each role.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from medicalbooking.modules.clinical.domain.shadow import ShadowReport

# Version of the code-defined rules behind the cut-offs. Changing a cut-off changes this version.
RULE_VERSION = "sombra-hallazgos-1"

ROLES_BY_FINDING: dict[str, tuple[str, ...]] = {
    "ANEMIA": ("hemoglobina",),
    "HIERRO": ("ferritina", "pcr"),
    "DIABETES": ("glucosa_ayuno", "hba1c"),
}

FUENTE_BY_FINDING: dict[str, str] = {
    "ANEMIA": "WHO 2024, Tablas 2-3",
    "HIERRO": "WHO 2020, Tabla 1",
    "DIABETES": "ADA 2026, Tablas 2.1-2.2 y recomendacion 2.4",
}


@dataclass(frozen=True)
class SnapshotItem:
    hallazgo: str
    categoria: str
    corte_valor: Decimal | None
    corte_unidad: str | None
    fuente: str
    motivo: str | None


def snapshot_items(report: ShadowReport) -> tuple[SnapshotItem, ...]:
    anemia = report.anemia
    hierro = report.hierro
    diabetes = report.diabetes
    return (
        SnapshotItem(
            hallazgo="ANEMIA",
            categoria=anemia.categoria,
            corte_valor=None if anemia.corte_g_l is None else Decimal(anemia.corte_g_l),
            corte_unidad=None if anemia.corte_g_l is None else "g/L",
            fuente=FUENTE_BY_FINDING["ANEMIA"],
            motivo=anemia.motivo,
        ),
        SnapshotItem(
            hallazgo="HIERRO",
            categoria=hierro.categoria,
            corte_valor=hierro.corte_ug_l,
            corte_unidad=None if hierro.corte_ug_l is None else "ug/L",
            fuente=FUENTE_BY_FINDING["HIERRO"],
            motivo=hierro.motivo,
        ),
        SnapshotItem(
            hallazgo="DIABETES",
            categoria=diabetes.categoria,
            corte_valor=None,
            corte_unidad=None,
            fuente=FUENTE_BY_FINDING["DIABETES"],
            motivo=diabetes.motivo,
        ),
    )


__all__ = [
    "FUENTE_BY_FINDING",
    "ROLES_BY_FINDING",
    "RULE_VERSION",
    "SnapshotItem",
    "snapshot_items",
]

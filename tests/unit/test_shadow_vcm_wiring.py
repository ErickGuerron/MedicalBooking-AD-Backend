"""The MCV limit comes from validated references through the shadow service (S20). Fake store."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from medicalbooking.modules.clinical.application.ports import OrderForEvaluation
from medicalbooking.modules.clinical.application.shadow_findings import ShadowFindingsService
from medicalbooking.modules.clinical.domain.anemia import ANEMIA_MODERADA
from medicalbooking.modules.clinical.domain.microcitosis import NO_EVALUABLE, SENAL_MICROCITOSIS
from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.identity.application.ports import AccessProfile

ANALYTE_ID = "a3000000-0000-4000-8000-000000000001"
ACTOR = AccessProfile(
    user_id="medico-1",
    status="ACTIVO",
    roles=frozenset({"MEDICO"}),
    permissions=frozenset({"resultados.leer_con_acceso"}),
)


def vcm_reference(**overrides) -> Reference:
    base = dict(
        id="r-vcm",
        version=1,
        unidad="fL",
        minimo=Decimal("80"),
        maximo=Decimal("100"),
        sexo=None,
        edad_min_dias=None,
        edad_max_dias=None,
        metodo=None,
        tipo_muestra=None,
        contexto=None,
        fecha_inicio=date(2026, 1, 1),
        fecha_fin=None,
    )
    base.update(overrides)
    return Reference(**base)


class FakeStore:
    def __init__(self, references: list[Reference], analyte_id: str | None = ANALYTE_ID) -> None:
        self._references = references
        self._analyte_id = analyte_id
        self.audit: list[object] = []

    def load_order_for_evaluation(self, order_id: str) -> OrderForEvaluation:
        return OrderForEvaluation(
            order_id=order_id,
            estado="EN_PROCESO",
            paciente_id="paciente-1",
            paciente_user_id="user-paciente",
            medico_user_id="medico-1",
            results=(),
        )

    def loinc_results_for_order(self, order_id: str):
        return [
            (
                "718-7",
                "g/dL",
                Decimal("10.0"),
            ),  # hemoglobin 100 g/L: moderate anaemia in a non-pregnant woman
            ("787-2", "fL", Decimal("72")),
        ]

    def pregnancy_states_for_order(self, order_id: str) -> list[str | None]:
        return ["NO"]

    def patient_sex_birth(self, paciente_id: str):
        return "F", date(1996, 1, 1)

    def patient_clinical_profile(self, paciente_id: str):
        return 0, False, None

    def analyte_id_by_loinc(self, codigo_loinc: str) -> str | None:
        assert codigo_loinc == "787-2"
        return self._analyte_id

    def list_validated_references(self, analito_id: str, on: date) -> list[Reference]:
        return list(self._references)

    def record_audit(self, event: object) -> None:
        self.audit.append(event)


def service(store: FakeStore) -> ShadowFindingsService:
    return ShadowFindingsService(store, clock=lambda: datetime(2026, 11, 9, 12, 0))


def test_a_validated_reference_turns_the_signal_on_and_leaves_anaemia_alone() -> None:
    report = service(FakeStore([vcm_reference()])).get(ACTOR, "orden-1")

    assert report.microcitosis is not None
    assert report.microcitosis.categoria == SENAL_MICROCITOSIS
    assert report.anemia.categoria == ANEMIA_MODERADA


def test_without_the_analyte_the_signal_stays_not_evaluable() -> None:
    report = service(FakeStore([], analyte_id=None)).get(ACTOR, "orden-1")

    assert report.microcitosis.categoria == NO_EVALUABLE
    assert "rango de referencia validado" in report.microcitosis.motivo


def test_a_restricted_reference_is_not_used() -> None:
    report = service(FakeStore([vcm_reference(contexto="EMBARAZO")])).get(ACTOR, "orden-1")

    assert report.microcitosis.categoria == NO_EVALUABLE


def test_the_signal_does_not_change_the_iron_finding() -> None:
    with_ref = service(FakeStore([vcm_reference()])).get(ACTOR, "orden-1")
    without_ref = service(FakeStore([], analyte_id=None)).get(ACTOR, "orden-1")

    assert with_ref.hierro == without_ref.hierro

"""Knowledge listings (feature doc S3, S4). Service rules over a fake store.

Drafts are visible only to conocimiento.validar or conocimiento.administrar. A reader with
conocimiento.leer alone sees validated knowledge only, and never a draft.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from medicalbooking.modules.clinical.application.knowledge import KnowledgeService
from medicalbooking.modules.clinical.domain.evaluation import CriterionSpec
from medicalbooking.modules.clinical.domain.references import Reference
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Forbidden, NotFound, ValidationFailed

ANALYTE = "a3000000-0000-4000-8000-000000000001"
OTHER_ANALYTE = "a3000000-0000-4000-8000-000000000002"


def _actor(user_id: str, *permissions: str) -> AccessProfile:
    return AccessProfile(
        user_id=user_id,
        status="ACTIVO",
        roles=frozenset(),
        permissions=frozenset(permissions),
    )


READER = _actor("lector-1", "conocimiento.leer")
VALIDATOR = _actor("validador-1", "conocimiento.leer", "conocimiento.validar")
ADMIN = _actor("admin-1", "conocimiento.leer", "conocimiento.administrar")
NOBODY = _actor("nadie-1")


def _rule(codigo: str, estado: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"regla-{codigo}",
        codigo=codigo,
        version=1,
        nombre=f"Regla {codigo}",
        descripcion=None,
        tipo="ALERTA",
        expresion_logica="TODOS",
        mensaje="Revisar el resultado",
        prioridad="INFORMATIVA",
        estado=estado,
        criteria=(
            CriterionSpec(analito_id=ANALYTE, operador="LT", umbral=Decimal("12"), unidad="g/dL"),
        ),
    )


def _reference(estado: str, analito: str = ANALYTE) -> SimpleNamespace:
    return SimpleNamespace(
        reference=Reference(
            id=f"ref-{estado}-{analito[-1]}",
            version=1,
            unidad="g/dL",
            minimo=Decimal("12.0"),
            maximo=Decimal("17.5"),
            sexo=None,
            edad_min_dias=None,
            edad_max_dias=None,
            metodo=None,
            tipo_muestra=None,
            contexto=None,
            fecha_inicio=date(2020, 1, 1),
            fecha_fin=None,
        ),
        analito_id=analito,
        estado=estado,
    )


class FakeStore:
    def __init__(self, rules=(), references=()) -> None:
        self.rules = list(rules)
        self.references = list(references)
        self.analytes = {ANALYTE, OTHER_ANALYTE}
        self.rule_queries: list[tuple[str, ...]] = []
        self.reference_queries: list[tuple[str | None, tuple[str, ...]]] = []
        self.validated_queries: list[tuple[str, date]] = []
        self.created: list[object] = []

    def analyte_exists(self, analito_id: str) -> bool:
        return analito_id in self.analytes

    def source_exists(self, fuente_id: str) -> bool:
        return True

    def list_rules(self, estados):
        self.rule_queries.append(tuple(estados))
        return [rule for rule in self.rules if rule.estado in estados]

    def list_references(self, analito_id, estados):
        self.reference_queries.append((analito_id, tuple(estados)))
        return [
            row
            for row in self.references
            if row.estado in estados and (analito_id is None or row.analito_id == analito_id)
        ]

    def list_validated_references(self, analito_id: str, on: date):
        self.validated_queries.append((analito_id, on))
        return [
            row.reference
            for row in self.references
            if row.estado == "VALIDADA" and row.analito_id == analito_id
        ]

    def create_rule(self, command, now: datetime) -> int:
        self.created.append(command)
        return 1


def service(store: FakeStore) -> KnowledgeService:
    return KnowledgeService(store, clock=lambda: datetime(2026, 11, 11, 12, 0))


# ---------- S3: rules ----------


def test_a_reader_gets_only_validated_rules() -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA"), _rule("HB_BORRADOR", "BORRADOR")])

    rules = service(store).list_rules(READER, estado=None)

    assert [rule.codigo for rule in rules] == ["HB_BAJA"]
    assert store.rule_queries == [("VALIDADA",)]


def test_a_validator_sees_drafts_with_their_state() -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA"), _rule("HB_BORRADOR", "BORRADOR")])

    rules = service(store).list_rules(VALIDATOR, estado=None)

    assert sorted((rule.codigo, rule.estado) for rule in rules) == [
        ("HB_BAJA", "VALIDADA"),
        ("HB_BORRADOR", "BORRADOR"),
    ]


def test_an_administrator_sees_drafts_too() -> None:
    store = FakeStore(rules=[_rule("HB_BORRADOR", "BORRADOR")])

    rules = service(store).list_rules(ADMIN, estado=None)

    assert [(rule.codigo, rule.estado) for rule in rules] == [("HB_BORRADOR", "BORRADOR")]


def test_a_reader_asking_for_drafts_is_refused_and_no_query_runs() -> None:
    store = FakeStore(rules=[_rule("HB_BORRADOR", "BORRADOR")])

    with pytest.raises(Forbidden):
        service(store).list_rules(READER, estado="BORRADOR")

    assert store.rule_queries == []


def test_a_validator_filtering_by_validated_gets_only_validated_rules() -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA"), _rule("HB_BORRADOR", "BORRADOR")])

    rules = service(store).list_rules(VALIDATOR, estado="VALIDADA")

    assert [rule.codigo for rule in rules] == ["HB_BAJA"]
    assert store.rule_queries == [("VALIDADA",)]


def test_each_rule_row_carries_its_criteria_and_state() -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA")])

    rule = service(store).list_rules(READER, estado=None)[0]

    criterion = rule.criteria[0]
    assert rule.estado == "VALIDADA"
    assert (criterion.analito_id, criterion.operador, criterion.umbral, criterion.unidad) == (
        ANALYTE,
        "LT",
        Decimal("12"),
        "g/dL",
    )


@pytest.mark.parametrize("estado", ["ARCHIVADA", "validada", ""])
def test_an_unknown_rule_state_is_rejected(estado: str) -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA")])

    with pytest.raises(ValidationFailed):
        service(store).list_rules(VALIDATOR, estado=estado)


def test_an_actor_without_knowledge_permission_cannot_list_rules() -> None:
    store = FakeStore(rules=[_rule("HB_BAJA", "VALIDADA")])

    with pytest.raises(Forbidden):
        service(store).list_rules(NOBODY, estado=None)

    assert store.rule_queries == []


def test_creating_a_rule_still_stores_a_draft_for_an_administrator() -> None:
    store = FakeStore()

    rule_id = service(store).create_rule(
        ADMIN,
        codigo="HB_NUEVA",
        nombre="Hemoglobina nueva",
        descripcion=None,
        tipo="ALERTA",
        expresion_logica="TODOS",
        mensaje="Revisar",
        prioridad="INFORMATIVA",
        criteria=[
            CriterionSpec(analito_id=ANALYTE, operador="LT", umbral=Decimal("12"), unidad="g/dL")
        ],
    )

    assert isinstance(rule_id, str)
    assert [command.codigo for command in store.created] == ["HB_NUEVA"]


# ---------- S4: references ----------


def test_a_validator_lists_the_drafts_of_one_analyte() -> None:
    store = FakeStore(references=[_reference("BORRADOR"), _reference("VALIDADA")])

    references = service(store).list_references(VALIDATOR, analito_id=ANALYTE, estado="BORRADOR")

    assert [row.estado for row in references] == ["BORRADOR"]
    assert store.reference_queries == [(ANALYTE, ("BORRADOR",))]


def test_an_administrator_lists_drafts_too() -> None:
    store = FakeStore(references=[_reference("BORRADOR")])

    references = service(store).list_references(ADMIN, analito_id=ANALYTE, estado="BORRADOR")

    assert [row.reference.unidad for row in references] == ["g/dL"]


def test_a_draft_listing_needs_an_analyte() -> None:
    store = FakeStore(references=[_reference("BORRADOR")])

    with pytest.raises(ValidationFailed):
        service(store).list_references(VALIDATOR, analito_id=None, estado="BORRADOR")

    assert store.reference_queries == []


@pytest.mark.parametrize("actor", [READER, NOBODY], ids=["reader", "no-permission"])
def test_references_are_not_listed_without_validate_or_administer(actor: AccessProfile) -> None:
    store = FakeStore(references=[_reference("VALIDADA")])

    with pytest.raises(Forbidden):
        service(store).list_references(actor, analito_id=ANALYTE, estado="VALIDADA")

    assert store.reference_queries == []


@pytest.mark.parametrize("estado", ["ARCHIVADA", "borrador"])
def test_an_unknown_reference_state_is_rejected(estado: str) -> None:
    store = FakeStore(references=[_reference("VALIDADA")])

    with pytest.raises(ValidationFailed):
        service(store).list_references(VALIDATOR, analito_id=ANALYTE, estado=estado)


def test_without_a_state_both_states_of_the_analyte_are_listed() -> None:
    store = FakeStore(
        references=[
            _reference("BORRADOR"),
            _reference("VALIDADA"),
            _reference("VALIDADA", analito=OTHER_ANALYTE),
        ]
    )

    references = service(store).list_references(VALIDATOR, analito_id=ANALYTE, estado=None)

    assert sorted(row.estado for row in references) == ["BORRADOR", "VALIDADA"]
    assert [row.analito_id for row in references] == [ANALYTE, ANALYTE]


def test_a_listing_for_an_unknown_analyte_is_not_found() -> None:
    store = FakeStore(references=[_reference("VALIDADA")])

    with pytest.raises(NotFound):
        service(store).list_references(VALIDATOR, analito_id="no-existe", estado=None)


def test_the_validated_ranges_on_a_date_are_unchanged_for_a_reader() -> None:
    store = FakeStore(references=[_reference("BORRADOR"), _reference("VALIDADA")])

    references = service(store).list_validated_references(READER, ANALYTE, date(2026, 11, 11))

    assert [reference.id for reference in references] == ["ref-VALIDADA-1"]
    assert store.validated_queries == [(ANALYTE, date(2026, 11, 11))]

"""Append-only edit history of one shadow finding (feature doc S7). Service rules over a fake store.

Reading the history is permission-checked and ownership-checked like the shadow read. A missing
order, snapshot or finding is refused with the same body as a foreign one, and every read is
audited with its outcome. Reading never writes.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from medicalbooking.modules.clinical.application.ports import OrderForEvaluation
from medicalbooking.modules.clinical.application.shadow_findings import ShadowFindingsService
from medicalbooking.modules.clinical.domain.shadow_edits import DESCARTADO
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.shared.domain.errors import Forbidden

ORDER = "orden-1"
SNAPSHOT = "snapshot-1"
READ = "resultados.leer_con_acceso"
REVIEW = "revisiones.registrar"

OWNER = AccessProfile(
    user_id="medico-1",
    status="ACTIVO",
    roles=frozenset({"MEDICO"}),
    permissions=frozenset({READ, REVIEW}),
)
FOREIGN = AccessProfile(
    user_id="medico-2",
    status="ACTIVO",
    roles=frozenset({"MEDICO"}),
    permissions=frozenset({READ, REVIEW}),
)
NO_READ = AccessProfile(
    user_id="medico-1",
    status="ACTIVO",
    roles=frozenset({"MEDICO"}),
    permissions=frozenset({REVIEW}),
)


def _edit(edit_id: str, accion: str, anterior: str, nueva: str, motivo: str, minute: int):
    return SimpleNamespace(
        id=edit_id,
        snapshot_id=SNAPSHOT,
        hallazgo="ANEMIA",
        accion=accion,
        categoria_anterior=anterior,
        categoria_nueva=nueva,
        motivo=motivo,
        medico_user_id="medico-1",
        creado_en=datetime(2026, 11, 11, 12, minute),
    )


class FakeStore:
    def __init__(self, edits=()) -> None:
        self.edits = list(edits)
        self.audit: list[object] = []
        self.writes = 0
        self.history_reads = 0

    def load_order_for_evaluation(self, order_id: str) -> OrderForEvaluation | None:
        if order_id != ORDER:
            return None
        return OrderForEvaluation(
            order_id=ORDER,
            estado="EN_PROCESO",
            paciente_id="paciente-1",
            paciente_user_id="user-paciente",
            medico_user_id="medico-1",
            results=(),
        )

    def shadow_item_category(self, order_id: str, snapshot_id: str, hallazgo: str):
        if (order_id, snapshot_id, hallazgo) == (ORDER, SNAPSHOT, "ANEMIA"):
            return "ANEMIA_LEVE"
        return None

    def list_shadow_edits(self, snapshot_id: str, hallazgo: str):
        self.history_reads += 1
        return [e for e in self.edits if e.snapshot_id == snapshot_id and e.hallazgo == hallazgo]

    def record_shadow_edit(
        self, snapshot_id, hallazgo, accion, anterior, nueva, motivo, medico_user_id, now
    ) -> str:
        self.writes += 1
        edit_id = f"edit-{len(self.edits) + 1}"
        self.edits.append(
            _edit(edit_id, accion, anterior, nueva, motivo, minute=len(self.edits) + 1)
        )
        return edit_id

    def record_audit(self, event) -> None:
        self.audit.append(event)

    # Support for the shadow read (GET sombra), which shares the authorization path.
    def loinc_results_for_order(self, order_id: str):
        return []

    def pregnancy_states_for_order(self, order_id: str):
        return []

    def patient_sex_birth(self, paciente_id: str):
        return None, None

    def patient_clinical_profile(self, paciente_id: str):
        return 0, False, None

    def analyte_id_by_loinc(self, codigo_loinc: str):
        return None

    def list_validated_references(self, analito_id: str, on):
        return []


def service(store: FakeStore) -> ShadowFindingsService:
    return ShadowFindingsService(store, clock=lambda: datetime(2026, 11, 11, 12, 0))


def _two_edits() -> list[SimpleNamespace]:
    return [
        _edit("e-1", "CONFIRMAR", "ANEMIA_LEVE", "ANEMIA_LEVE", "coincide con la clinica", 1),
        _edit("e-2", "MODIFICAR", "ANEMIA_LEVE", "SIN_ANEMIA", "altitud registrada", 5),
    ]


def test_the_owner_reads_every_edit_of_one_finding_with_its_decision_fields() -> None:
    store = FakeStore(_two_edits())

    rows = service(store).edit_history(OWNER, ORDER, SNAPSHOT, "ANEMIA")

    assert [
        (r.id, r.accion, r.categoria_anterior, r.categoria_nueva, r.motivo, r.medico_user_id)
        for r in rows
    ] == [
        ("e-1", "CONFIRMAR", "ANEMIA_LEVE", "ANEMIA_LEVE", "coincide con la clinica", "medico-1"),
        ("e-2", "MODIFICAR", "ANEMIA_LEVE", "SIN_ANEMIA", "altitud registrada", "medico-1"),
    ]
    assert rows[1].creado_en == datetime(2026, 11, 11, 12, 5)


def test_every_successful_read_is_audited_as_ok() -> None:
    store = FakeStore(_two_edits())

    service(store).edit_history(OWNER, ORDER, SNAPSHOT, "ANEMIA")

    event = store.audit[-1]
    assert (event.entidad, event.accion, event.resultado) == (
        "hallazgo_sombra_ediciones",
        "consultar",
        "OK",
    )


def test_a_finding_without_edits_has_an_empty_history() -> None:
    store = FakeStore()

    assert service(store).edit_history(OWNER, ORDER, SNAPSHOT, "ANEMIA") == []
    assert store.audit[-1].resultado == "OK"


def test_a_foreign_doctor_is_refused_and_the_denial_is_audited() -> None:
    store = FakeStore(_two_edits())

    with pytest.raises(Forbidden):
        service(store).edit_history(FOREIGN, ORDER, SNAPSHOT, "ANEMIA")

    assert store.audit[-1].resultado == "DENEGADO"
    assert store.history_reads == 0


def test_a_missing_order_gets_the_same_refusal_as_a_foreign_one() -> None:
    store = FakeStore(_two_edits())
    with pytest.raises(Forbidden) as foreign:
        service(store).edit_history(FOREIGN, ORDER, SNAPSHOT, "ANEMIA")

    with pytest.raises(Forbidden) as missing:
        service(store).edit_history(OWNER, "orden-inexistente", SNAPSHOT, "ANEMIA")

    assert (missing.value.status, str(missing.value)) == (
        foreign.value.status,
        str(foreign.value),
    )
    assert store.history_reads == 0


@pytest.mark.parametrize(
    ("snapshot_id", "hallazgo"),
    [(SNAPSHOT, "TIROIDES"), ("snapshot-otro", "ANEMIA")],
    ids=["unknown-finding", "unknown-snapshot"],
)
def test_an_unknown_snapshot_or_finding_gets_the_same_refusal(
    snapshot_id: str, hallazgo: str
) -> None:
    store = FakeStore(_two_edits())
    with pytest.raises(Forbidden) as foreign:
        service(store).edit_history(FOREIGN, ORDER, SNAPSHOT, "ANEMIA")

    with pytest.raises(Forbidden) as unknown:
        service(store).edit_history(OWNER, ORDER, snapshot_id, hallazgo)

    assert str(unknown.value) == str(foreign.value)
    assert store.audit[-1].resultado == "DENEGADO"
    assert store.history_reads == 0


def test_without_the_read_permission_nothing_is_read() -> None:
    store = FakeStore(_two_edits())

    with pytest.raises(Forbidden):
        service(store).edit_history(NO_READ, ORDER, SNAPSHOT, "ANEMIA")

    assert store.audit[-1].resultado == "DENEGADO"
    assert store.history_reads == 0


def test_reading_the_history_writes_nothing() -> None:
    store = FakeStore(_two_edits())
    before = list(store.edits)

    service(store).edit_history(OWNER, ORDER, SNAPSHOT, "ANEMIA")

    assert store.writes == 0
    assert store.edits == before


def test_a_new_edit_is_appended_and_shows_up_after_the_earlier_ones() -> None:
    store = FakeStore(_two_edits())

    edit_id, categoria = service(store).edit_finding(
        OWNER,
        ORDER,
        SNAPSHOT,
        "ANEMIA",
        accion="DESCARTAR",
        categoria_nueva=None,
        motivo="falso positivo",
    )
    rows = service(store).edit_history(OWNER, ORDER, SNAPSHOT, "ANEMIA")

    assert (edit_id, categoria) == ("edit-3", DESCARTADO)
    assert [r.id for r in rows] == ["e-1", "e-2", "edit-3"]
    assert rows[0] == _two_edits()[0]


def test_the_shadow_read_keeps_answering_for_the_owner_and_is_audited() -> None:
    store = FakeStore()

    service(store).get(OWNER, ORDER)

    event = store.audit[-1]
    assert (event.entidad, event.accion, event.resultado) == (
        "modo_sombra",
        "consultar",
        "OK",
    )

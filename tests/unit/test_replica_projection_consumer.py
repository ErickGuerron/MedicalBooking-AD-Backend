from contextlib import contextmanager

import pytest

from medicalbooking.modules.replica.application.projection_consumer import apply_event

EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
USER = "77777777-7777-4777-8777-777777777777"
PATIENT = "11111111-1111-4111-8111-111111111111"
ORDER = "44444444-4444-4444-8444-444444444444"
DETAIL = "55555555-5555-4555-8555-555555555555"
RESULT = "66666666-6666-4666-8666-666666666666"
PROPOSAL = "88888888-8888-4888-8888-888888888888"
HALLAZGO = "99999999-9999-4999-8999-999999999999"


class FakeSink:
    """Stages writes inside a transaction; keeps them only when the block completes."""

    def __init__(self) -> None:
        self.committed: list[tuple] = []
        self.processed: set[str] = set()
        self._staged: list[tuple] = []
        self._staged_processed: set[str] = set()
        self.fail_on: str | None = None

    @contextmanager
    def transaction(self):
        self._staged, self._staged_processed = [], set()
        try:
            yield self
        except Exception:
            self._staged, self._staged_processed = [], set()
            raise
        self.committed.extend(self._staged)
        self.processed |= self._staged_processed

    def mark_processed(self, event_id: str, consumer: str, tipo: str) -> bool:
        if event_id in self.processed or event_id in self._staged_processed:
            return False
        self._staged_processed.add(event_id)
        return True

    def _write(self, name: str, *args) -> None:
        if self.fail_on == name:
            raise RuntimeError("write rejected")
        self._staged.append((name, *args))

    def upsert_account(self, usuario_id, estado):
        self._write("upsert_account", usuario_id, estado)

    def replace_roles(self, usuario_id, roles):
        self._write("replace_roles", usuario_id, tuple(roles))

    def upsert_patient(self, **fields):
        self._write("upsert_patient", tuple(sorted(fields.items())))

    def upsert_order(self, orden):
        self._write(
            "upsert_order", tuple(sorted((k, str(v)) for k, v in orden.items() if k != "detalles"))
        )

    def upsert_detail(self, detalle_id, orden_id, estado):
        self._write("upsert_detail", detalle_id, orden_id, estado)

    def set_order_state(self, orden_id, estado):
        self._write("set_order_state", orden_id, estado)

    def upsert_results(self, detalle_id, resultados):
        self._write("upsert_results", detalle_id, tuple(r["id"] for r in resultados))

    def upsert_proposal(self, propuesta, hallazgos):
        self._write(
            "upsert_proposal",
            propuesta["id"],
            propuesta["estado"],
            tuple(h["id"] for h in hallazgos),
        )

    def delete_proposal(self, propuesta_id):
        self._write("delete_proposal", propuesta_id)


def _envelope(event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> dict:
    return {
        "event_id": EVENT_ID,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "aggregate_version": 1,
        "created_at": "2026-11-12T18:00:00+00:00",
        "payload": payload,
    }


def _names(sink: FakeSink) -> list[str]:
    return [entry[0] for entry in sink.committed]


def test_usuario_creado_projects_the_account_roles_and_patient_names():
    sink = FakeSink()
    event = _envelope(
        "usuario.creado",
        "usuario",
        USER,
        {
            "usuario": {"id": USER, "estado": "PENDIENTE_VERIFICACION"},
            "roles": [1],
            "paciente": {"id": PATIENT, "usuario_id": USER, "nombre": "Ana", "apellido": "S"},
        },
    )

    assert apply_event(event, sink, consumer="replica-supabase") is True

    assert _names(sink) == ["upsert_account", "replace_roles", "upsert_patient"]
    patient = dict(sink.committed[2][1])
    assert patient["nombre"] == "Ana"
    assert patient["apellido"] == "S"


def test_a_user_without_patient_projects_no_patient():
    sink = FakeSink()
    event = _envelope(
        "usuario.creado",
        "usuario",
        USER,
        {"usuario": {"id": USER, "estado": "ACTIVO"}, "roles": [2], "paciente": None},
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert "upsert_patient" not in _names(sink)


def test_usuario_activado_sets_the_account_state():
    sink = FakeSink()
    event = _envelope(
        "usuario.activado", "usuario", USER, {"usuario": {"id": USER, "estado": "ACTIVO"}}
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert sink.committed == [("upsert_account", USER, "ACTIVO")]


def test_orden_creada_projects_the_order_and_its_detail_lines():
    sink = FakeSink()
    orden = {
        "id": ORDER,
        "paciente_id": PATIENT,
        "medico_id": "c",
        "fecha_orden": "2026-11-12T18:00:00",
        "prioridad": "NORMAL",
        "estado": "SOLICITADA",
        "detalles": [{"id": DETAIL, "tipo_prueba_id": "t", "estado": "PENDIENTE"}],
    }
    event = _envelope(
        "orden_laboratorio.creada",
        "orden_laboratorio",
        ORDER,
        {"estado": "SOLICITADA", "orden": orden},
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert _names(sink) == ["upsert_order", "upsert_detail"]
    assert sink.committed[1] == ("upsert_detail", DETAIL, ORDER, "PENDIENTE")


def test_resultado_registrado_projects_detail_order_state_and_results():
    sink = FakeSink()
    event = _envelope(
        "resultado_laboratorio.registrado",
        "orden_laboratorio",
        ORDER,
        {
            "detalle_id": DETAIL,
            "estado_orden": "COMPLETADA",
            "orden_estado": "COMPLETADA",
            "detalle": {
                "id": DETAIL,
                "estado": "COMPLETADO",
                "fecha_resultado": "2026-11-12T18:00:00",
            },
            "resultados": [{"id": RESULT, "nombre_analito": "Hemoglobina", "validado_en": "x"}],
        },
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert _names(sink) == ["upsert_detail", "set_order_state", "upsert_results"]
    assert sink.committed[1] == ("set_order_state", ORDER, "COMPLETADA")
    assert sink.committed[2] == ("upsert_results", DETAIL, (RESULT,))


def test_an_accepted_proposal_is_projected_with_its_findings():
    sink = FakeSink()
    event = _envelope(
        "propuesta.revisada",
        "propuesta_clinica",
        PROPOSAL,
        {
            "propuesta": {
                "id": PROPOSAL,
                "orden_id": ORDER,
                "estado": "ACEPTADA",
                "resumen_propuesto": "t",
            },
            "hallazgos": [{"id": HALLAZGO, "propuesta_id": PROPOSAL, "tipo": "PREDIABETES"}],
            "decision": "ACEPTAR",
        },
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert sink.committed == [("upsert_proposal", PROPOSAL, "ACEPTADA", (HALLAZGO,))]


def test_a_rejected_proposal_is_removed_from_the_replica():
    sink = FakeSink()
    event = _envelope(
        "propuesta.revisada",
        "propuesta_clinica",
        PROPOSAL,
        {
            "propuesta": {"id": PROPOSAL, "orden_id": ORDER, "estado": "RECHAZADA"},
            "hallazgos": [],
            "decision": "RECHAZAR",
        },
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert sink.committed == [("delete_proposal", PROPOSAL)]


def test_a_duplicate_event_is_applied_once():
    sink = FakeSink()
    event = _envelope(
        "usuario.activado", "usuario", USER, {"usuario": {"id": USER, "estado": "ACTIVO"}}
    )

    assert apply_event(event, sink, consumer="replica-supabase") is True
    assert apply_event(event, sink, consumer="replica-supabase") is False

    assert len([e for e in sink.committed if e[0] == "upsert_account"]) == 1


def test_a_failed_write_rolls_back_the_projection_and_the_processed_mark():
    sink = FakeSink()
    sink.fail_on = "upsert_account"
    event = _envelope(
        "usuario.activado", "usuario", USER, {"usuario": {"id": USER, "estado": "ACTIVO"}}
    )

    with pytest.raises(RuntimeError):
        apply_event(event, sink, consumer="replica-supabase")

    assert sink.committed == []
    assert EVENT_ID not in sink.processed


def test_an_unknown_event_type_is_ignored_without_writes():
    sink = FakeSink()
    event = _envelope("cita.reservada", "cita", USER, {"x": 1})

    apply_event(event, sink, consumer="replica-supabase")

    assert sink.committed == []

"""Consumer routing for tratamientos and seguimientos (treatments S11).

The fake transaction records the calls. The replica keeps only the replicated columns, and the
treatment upsert receives the envelope's aggregate version so the sink can ignore a stale event.
The existing dispatch entries are checked too: the new events must not change them.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from medicalbooking.modules.replica.application.projection_consumer import _HANDLERS, apply_event

EVENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TRATAMIENTO = "11111111-1111-4111-8111-111111111111"
SEGUIMIENTO = "22222222-2222-4222-8222-222222222222"
CITA = "33333333-3333-4333-8333-333333333333"
PACIENTE = "44444444-4444-4444-8444-444444444444"
MEDICO = "55555555-5555-4555-8555-555555555555"
DESCRIPCION = "Fisioterapia lumbar, control semanal con ejercicios de movilidad (sintetico)"
NOTA = "Paciente refiere mejoria del dolor; continuar ejercicios de movilidad (sintetico)."


class FakeTx:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def mark_processed(self, event_id, consumer, tipo, created_at) -> bool:
        return True

    def upsert_tratamiento(self, tratamiento, version) -> None:
        self.calls.append(("upsert_tratamiento", tratamiento, version))

    def upsert_seguimiento(self, seguimiento) -> None:
        self.calls.append(("upsert_seguimiento", seguimiento))

    def upsert_cita(self, cita) -> None:
        self.calls.append(("upsert_cita", cita))


class FakeSink:
    def __init__(self) -> None:
        self.tx = FakeTx()

    @contextmanager
    def transaction(self):
        yield self.tx


def _envelope(event_type: str, aggregate_id: str, payload: dict, version: int) -> dict:
    return {
        "event_id": EVENT_ID,
        "aggregate_type": "tratamiento",
        "aggregate_id": aggregate_id,
        "event_type": event_type,
        "aggregate_version": version,
        "created_at": "2026-11-14T12:00:00+00:00",
        "payload": payload,
    }


def _tratamiento_payload(descripcion: str = DESCRIPCION, estado: str = "ACTIVO") -> dict:
    return {
        "tratamiento": {
            "id": TRATAMIENTO,
            "cita_id": CITA,
            "paciente_id": PACIENTE,
            "medico_id": MEDICO,
            "descripcion": descripcion,
            "estado": estado,
            "fecha_inicio": "2026-11-01",
            "fecha_fin": None,
            "medico_user_id": "must-not-reach-the-replica",
        }
    }


def test_tratamiento_creado_upserts_the_treatment_with_the_envelope_version() -> None:
    sink = FakeSink()
    envelope = _envelope("tratamiento.creado", TRATAMIENTO, _tratamiento_payload(), 1)

    assert apply_event(envelope, sink, "replica-supabase") is True

    assert sink.tx.calls == [
        (
            "upsert_tratamiento",
            {
                "id": TRATAMIENTO,
                "cita_id": CITA,
                "paciente_id": PACIENTE,
                "medico_id": MEDICO,
                "descripcion": DESCRIPCION,
                "estado": "ACTIVO",
                "fecha_inicio": "2026-11-01",
                "fecha_fin": None,
            },
            1,
        )
    ]


def test_tratamiento_actualizado_upserts_the_full_description_and_the_new_version() -> None:
    sink = FakeSink()
    envelope = _envelope(
        "tratamiento.actualizado",
        TRATAMIENTO,
        _tratamiento_payload(
            descripcion="Control a las dos semanas (sintetico)", estado="FINALIZADO"
        ),
        4,
    )

    apply_event(envelope, sink, "replica-supabase")

    _, tratamiento, version = sink.tx.calls[0]
    assert tratamiento["descripcion"] == "Control a las dos semanas (sintetico)"
    assert tratamiento["estado"] == "FINALIZADO"
    assert version == 4


def test_seguimiento_registrado_upserts_the_full_note_and_drops_no_text() -> None:
    sink = FakeSink()
    payload = {
        "seguimiento": {
            "id": SEGUIMIENTO,
            "tratamiento_id": TRATAMIENTO,
            "secuencia": 7,
            "nota": NOTA,
            "creado_en": "2026-11-14T12:00:00+00:00",
        }
    }

    apply_event(_envelope("seguimiento.registrado", TRATAMIENTO, payload, 3), sink, "replica")

    assert sink.tx.calls == [
        (
            "upsert_seguimiento",
            {
                "id": SEGUIMIENTO,
                "tratamiento_id": TRATAMIENTO,
                "secuencia": 7,
                "nota": NOTA,
                "creado_en": "2026-11-14T12:00:00+00:00",
            },
        )
    ]


def test_existing_dispatch_entries_keep_their_handlers() -> None:
    previous = {
        "usuario.creado",
        "usuario.activado",
        "usuario.eliminado",
        "orden_laboratorio.creada",
        "resultado_laboratorio.registrado",
        "propuesta.revisada",
        "factura.creada",
        "pago.actualizado",
        "reembolso.actualizado",
        "cita.actualizada",
        "cita.eliminada",
    }

    assert previous <= set(_HANDLERS)


def test_cita_actualizada_still_upserts_the_appointment() -> None:
    sink = FakeSink()
    cita = {"id": CITA, "paciente_id": PACIENTE, "servicio_id": MEDICO, "estado": "RESERVADA"}
    envelope = _envelope("cita.actualizada", CITA, {"cita": cita}, 2)

    apply_event(envelope, sink, "replica")

    assert [call[0] for call in sink.tx.calls] == ["upsert_cita"]
    assert sink.tx.calls[0][1]["estado"] == "RESERVADA"

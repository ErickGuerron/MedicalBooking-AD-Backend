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

    def mark_processed(self, event_id: str, consumer: str, tipo: str, created_at: str) -> bool:
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

    def upsert_detail(self, detalle_id, orden_id, estado, tipo_prueba_id):
        self._write("upsert_detail", detalle_id, orden_id, estado, tipo_prueba_id)

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

    def upsert_invoice(self, factura):
        self._write("upsert_invoice", dict(factura))

    def upsert_invoice_detail(self, detalle, factura_id):
        self._write("upsert_invoice_detail", dict(detalle), factura_id)

    def set_invoice_state(self, factura_id, estado):
        self._write("set_invoice_state", factura_id, estado)

    def upsert_payment(self, pago):
        self._write("upsert_payment", dict(pago))

    def upsert_refund(self, reembolso):
        self._write("upsert_refund", dict(reembolso))

    def upsert_cita(self, cita):
        self._write("upsert_cita", dict(cita))


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
    assert sink.committed[1] == ("upsert_detail", DETAIL, ORDER, "PENDIENTE", "t")


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


FACTURA = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DETALLE_FACTURA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CITA = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
SERVICIO = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
PAGO = "f0f0f0f0-f0f0-4f0f-8f0f-f0f0f0f0f0f0"
REEMBOLSO = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
FORBIDDEN = ("stripe", "clave")

FACTURA_KEYS = {
    "id",
    "paciente_id",
    "cita_id",
    "numero_factura",
    "subtotal",
    "impuestos",
    "total",
    "moneda",
    "estado",
    "creada_en",
}
PAGO_KEYS = {"id", "factura_id", "monto", "moneda", "estado", "creado_en", "pagado_en"}
REEMBOLSO_KEYS = {
    "id",
    "pago_id",
    "monto",
    "estado",
    "solicitado_en",
    "procesado_en",
    "motivo_codigo",
}


def _factura(estado: str) -> dict:
    return {
        "id": FACTURA,
        "paciente_id": PATIENT,
        "cita_id": CITA,
        "numero_factura": "FAC-000001",
        "subtotal": "25.00",
        "impuestos": "0.00",
        "total": "25.00",
        "moneda": "USD",
        "estado": estado,
        "creada_en": "2026-11-12T18:00:00",
    }


def _pago(estado: str) -> dict:
    return {
        "id": PAGO,
        "factura_id": FACTURA,
        "monto": "25.00",
        "moneda": "USD",
        "estado": estado,
        "creado_en": "2026-11-12T18:00:00",
        "pagado_en": None if estado == "PENDIENTE" else "2026-11-12T18:05:00",
    }


def _no_forbidden_writes(sink: FakeSink) -> None:
    rendered = repr(sink.committed).lower()
    for word in FORBIDDEN:
        assert word not in rendered, f"a write carried a '{word}' key"


def test_factura_creada_projects_the_invoice_and_its_detail_line():
    sink = FakeSink()
    detalle = {
        "id": DETALLE_FACTURA,
        "servicio_id": SERVICIO,
        "descripcion": "Consulta",
        "cantidad": 1,
        "precio_unitario": "25.00",
        "subtotal": "25.00",
    }
    event = _envelope(
        "factura.creada",
        "factura",
        FACTURA,
        {"factura": _factura("PENDIENTE"), "detalles": [detalle]},
    )

    assert apply_event(event, sink, consumer="replica-supabase") is True

    assert _names(sink) == ["upsert_invoice", "upsert_invoice_detail"]
    invoice = sink.committed[0][1]
    assert set(invoice) == FACTURA_KEYS
    assert invoice["estado"] == "PENDIENTE"
    assert sink.committed[1] == ("upsert_invoice_detail", detalle, FACTURA)
    _no_forbidden_writes(sink)


def test_pago_actualizado_projects_the_payment_and_the_invoice_state():
    sink = FakeSink()
    event = _envelope(
        "pago.actualizado",
        "pago",
        PAGO,
        {
            "pago": _pago("CONFIRMADO"),
            "factura": {"id": FACTURA, "paciente_id": PATIENT, "estado": "PAGADA"},
        },
    )

    apply_event(event, sink, consumer="replica-supabase")

    assert _names(sink) == ["set_invoice_state", "upsert_payment"]
    assert sink.committed[0] == ("set_invoice_state", FACTURA, "PAGADA")
    payment = sink.committed[1][1]
    assert set(payment) == PAGO_KEYS
    assert payment["estado"] == "CONFIRMADO"
    _no_forbidden_writes(sink)


def test_reembolso_actualizado_projects_the_refund_its_payment_and_its_invoice():
    sink = FakeSink()
    # A snapshot that carried gateway keys by mistake must not reach the replica.
    reembolso = {
        "id": REEMBOLSO,
        "pago_id": PAGO,
        "monto": "25.00",
        "estado": "PROCESADO",
        "solicitado_en": "2026-11-12T18:00:00",
        "procesado_en": "2026-11-12T19:00:00",
        "motivo_codigo": "CANCELACION_PACIENTE",
        "stripe_refund_id": "re_test_must_not_leak",
    }
    pago = {**_pago("CONFIRMADO"), "clave_idempotencia": "must-not-leak"}
    event = _envelope(
        "reembolso.actualizado",
        "reembolso",
        REEMBOLSO,
        {
            "reembolso": reembolso,
            "pago": pago,
            "factura": {"id": FACTURA, "paciente_id": PATIENT, "estado": "REEMBOLSADA"},
        },
    )

    assert apply_event(event, sink, consumer="replica-supabase") is True

    assert _names(sink) == ["set_invoice_state", "upsert_payment", "upsert_refund"]
    assert sink.committed[0] == ("set_invoice_state", FACTURA, "REEMBOLSADA")
    assert set(sink.committed[1][1]) == PAGO_KEYS
    refund = sink.committed[2][1]
    assert set(refund) == REEMBOLSO_KEYS
    assert refund["estado"] == "PROCESADO"
    _no_forbidden_writes(sink)


CITA_KEYS = {
    "id",
    "paciente_id",
    "servicio_id",
    "estado",
    "reservada_en",
    "confirmada_en",
    "cancelada_en",
    "motivo_cancelacion_codigo",
    "motivo_cancelacion_texto",
    "inicio",
    "fin",
}
CITA_PAYLOAD_EXTRA = {
    "motivo": "Dolor de pecho desde ayer",
    "clave_idempotencia": "must-not-leak",
    "version_fila": "AAAAAAAAAAE=",
    "horario_id": "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2",
    "expiracion_reserva": "2026-11-12T18:15:00",
}


def _cita(estado: str, **reason) -> dict:
    cita = {
        "id": CITA,
        "paciente_id": PATIENT,
        "servicio_id": SERVICIO,
        "estado": estado,
        "reservada_en": "2026-11-12T18:00:00",
        "confirmada_en": None,
        "cancelada_en": None,
        "motivo_cancelacion_codigo": None,
        "motivo_cancelacion_texto": None,
        "inicio": "2026-11-15T10:00:00",
        "fin": "2026-11-15T10:30:00",
    }
    cita.update(reason)
    return cita


def test_cita_actualizada_projects_only_the_replica_columns():
    sink = FakeSink()
    event = _envelope(
        "cita.actualizada",
        "cita",
        CITA,
        {"cita": {**_cita("PENDIENTE_PAGO"), **CITA_PAYLOAD_EXTRA}},
    )

    assert apply_event(event, sink, consumer="replica-supabase") is True

    assert _names(sink) == ["upsert_cita"]
    written = sink.committed[0][1]
    assert set(written) == CITA_KEYS
    assert written["estado"] == "PENDIENTE_PAGO"
    assert written["inicio"] == "2026-11-15T10:00:00"
    assert written["fin"] == "2026-11-15T10:30:00"
    _no_forbidden_writes(sink)
    assert "dolor de pecho" not in repr(sink.committed).lower()


def test_cita_actualizada_carries_a_doctor_reason_exactly_as_stored():
    sink = FakeSink()
    texto = ("Urgencia médica en el hospital. " * 60)[:1500]
    assert len(texto) == 1500
    event = _envelope(
        "cita.actualizada",
        "cita",
        CITA,
        {
            "cita": _cita(
                "CANCELADA",
                cancelada_en="2026-11-13T09:00:00",
                motivo_cancelacion_codigo="IMPREVISTO_MEDICO",
                motivo_cancelacion_texto=texto,
            )
        },
    )

    apply_event(event, sink, consumer="replica-supabase")

    written = sink.committed[0][1]
    assert written["motivo_cancelacion_codigo"] == "IMPREVISTO_MEDICO"
    assert written["motivo_cancelacion_texto"] == texto
    assert len(written["motivo_cancelacion_texto"]) == 1500


def test_cita_actualizada_from_a_patient_cancellation_projects_null_reason_fields():
    sink = FakeSink()
    event = _envelope(
        "cita.actualizada",
        "cita",
        CITA,
        {"cita": _cita("CANCELADA", cancelada_en="2026-11-13T09:00:00")},
    )

    apply_event(event, sink, consumer="replica-supabase")

    written = sink.committed[0][1]
    assert written["motivo_cancelacion_codigo"] is None
    assert written["motivo_cancelacion_texto"] is None

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AccountNotActive,
)
from medicalbooking.modules.replica.infrastructure.supabase_reader import SupabaseReplicaReader

USER = UUID("77777777-7777-7777-7777-777777777777")
PATIENT = UUID("11111111-1111-1111-1111-111111111111")
ORDER = UUID("44444444-4444-4444-4444-444444444444")
DETAIL = UUID("55555555-5555-5555-5555-555555555555")
RESULT = UUID("66666666-6666-6666-6666-666666666666")
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self._last: list = []

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.split())
        self._conn.log.append(("execute", normalized, params))
        if "set_config" in normalized:
            self._last = []
        elif "FROM replica.usuarios WHERE" in normalized:
            self._last = [(self._conn.estado,)] if self._conn.estado else []
        elif "FROM replica.usuarios_roles ur" in normalized:
            self._last = [(1,)] if self._conn.permiso else []
        elif "FROM replica.pacientes WHERE" in normalized:
            self._last = [(1,)] if self._conn.paciente_de_cuenta else []
        elif "FROM replica.propuestas_clinicas pc" in normalized:
            self._last = list(self._conn.proposal_rows)
        elif "FROM replica.ordenes_laboratorio o" in normalized:
            self._last = list(self._conn.order_rows)
        else:
            self._last = []

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self) -> list:
        return self._last


class FakeConnection:
    def __init__(
        self,
        estado="ACTIVO",
        permiso=True,
        paciente_de_cuenta=True,
        order_rows=(),
        proposal_rows=(),
    ) -> None:
        self.log: list = []
        self.estado = estado
        self.permiso = permiso
        self.paciente_de_cuenta = paciente_de_cuenta
        self.order_rows = list(order_rows)
        self.proposal_rows = list(proposal_rows)

    @contextmanager
    def transaction(self):
        self.log.append(("begin",))
        yield
        self.log.append(("commit",))

    @contextmanager
    def cursor(self):
        yield FakeCursor(self)


def _row(result_id=RESULT, analito="Glucosa"):
    return (
        ORDER, NOW, "COMPLETADA",
        DETAIL, "COMPLETADO",
        result_id, analito, "95", 95.0, "mg/dL", 70.0, 99.0, "NORMAL", NOW,
    )


def _proposal(estado, resumen="texto automatico", conclusion="conclusion medica",
              tipo="PREDIABETES", explicacion="glucosa en ayunas elevada"):
    return (ORDER, estado, resumen, conclusion, tipo, explicacion, 1)


def _executed(connection: FakeConnection) -> list[str]:
    return [entry[1] for entry in connection.log if entry[0] == "execute"]


def _read(connection: FakeConnection):
    return SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)


def test_the_patient_and_the_user_are_fixed_inside_the_transaction_before_any_select():
    connection = FakeConnection()

    _read(connection)

    assert connection.log[0] == ("begin",)
    statements = [e for e in connection.log if e[0] == "execute"]
    set_config_params = [e[2] for e in statements if "set_config" in e[1]]
    assert (str(PATIENT),) in set_config_params
    assert (str(USER),) in set_config_params
    first_select = next(i for i, e in enumerate(statements) if "set_config" not in e[1])
    assert all(
        i < first_select for i, e in enumerate(statements) if "set_config" in e[1]
    )
    assert connection.log[-1] == ("commit",)

def test_ids_are_parameters_never_part_of_the_sql_text():
    connection = FakeConnection()

    _read(connection)

    for statement in _executed(connection):
        assert str(PATIENT) not in statement
        assert str(USER) not in statement


def test_an_inactive_account_gets_nothing():
    connection = FakeConnection(estado="BLOQUEADO", order_rows=[_row()])

    with pytest.raises(AccountNotActive):
        _read(connection)

    assert not any("replica.ordenes_laboratorio o" in s for s in _executed(connection))


def test_a_missing_account_fails_closed():
    connection = FakeConnection(estado=None, order_rows=[_row()])

    with pytest.raises(AccountNotActive):
        _read(connection)

    assert not any("replica.ordenes_laboratorio o" in s for s in _executed(connection))


def test_a_user_without_the_own_results_permission_is_denied():
    connection = FakeConnection(permiso=False, order_rows=[_row()])

    with pytest.raises(AccessDenied):
        _read(connection)

    assert not any("replica.ordenes_laboratorio o" in s for s in _executed(connection))


def test_a_patient_that_does_not_belong_to_the_account_is_denied():
    connection = FakeConnection(paciente_de_cuenta=False, order_rows=[_row()])

    with pytest.raises(AccessDenied):
        _read(connection)

    assert not any("replica.ordenes_laboratorio o" in s for s in _executed(connection))


def test_the_permission_checked_is_the_one_the_sql_api_uses():
    connection = FakeConnection()

    _read(connection)

    permission_calls = [e for e in connection.log if e[0] == "execute"]
    permission_calls = [e for e in permission_calls if "usuarios_roles ur" in e[1]]
    assert permission_calls, "the permission must be checked"
    assert permission_calls[0][2] == (str(USER), "resultados.leer_propios")

def test_rows_are_grouped_into_orders_with_their_validated_results():
    connection = FakeConnection(order_rows=[_row(), _row(result_id=UUID(int=7), analito="HbA1c")])

    result = _read(connection)

    assert result.patient_id == PATIENT
    order = result.orders[0]
    assert order["id"] == str(ORDER)
    assert [item["analito"] for item in order["resultados"]] == ["Glucosa", "HbA1c"]


def test_an_order_without_results_still_comes_back_with_an_empty_list():
    row = (ORDER, NOW, "SOLICITADA", DETAIL, "PENDIENTE",
           None, None, None, None, None, None, None, None, None)

    result = _read(FakeConnection(order_rows=[row]))

    assert result.orders[0]["resultados"] == []


def test_a_corrected_proposal_shows_the_physician_conclusion_not_the_machine_text():
    connection = FakeConnection(
        order_rows=[_row()],
        proposal_rows=[_proposal("CORREGIDA")],
    )

    order = _read(connection).orders[0]

    assert order["diagnostico"]["conclusion"] == "conclusion medica"
    assert order["diagnostico"]["estado"] == "CORREGIDA"


def test_an_accepted_proposal_shows_its_approved_text():
    connection = FakeConnection(
        order_rows=[_row()],
        proposal_rows=[_proposal("ACEPTADA")],
    )

    order = _read(connection).orders[0]

    assert order["diagnostico"]["conclusion"] == "texto automatico"
    assert order["diagnostico"]["hallazgos"] == [
        {"tipo": "PREDIABETES", "explicacion": "glucosa en ayunas elevada"}
    ]


def test_an_order_without_a_reviewed_proposal_has_no_diagnosis():
    order = _read(FakeConnection(order_rows=[_row()], proposal_rows=[])).orders[0]

    assert order["diagnostico"] is None

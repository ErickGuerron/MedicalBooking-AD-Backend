from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from medicalbooking.modules.replica.application.fallback_read import AccountNotActive
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
        self._conn.log.append(("execute", " ".join(sql.split()), params))
        if "cuentas_acceso" in sql:
            self._last = [self._conn.account_row] if self._conn.account_row else []
        else:
            self._last = self._conn.order_rows

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self) -> list:
        return self._last


class FakeConnection:
    def __init__(self, account_row=("ACTIVO",), order_rows=()) -> None:
        self.log: list = []
        self.account_row = account_row
        self.order_rows = list(order_rows)

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


def _sql(connection: FakeConnection) -> list[str]:
    return [entry[1] for entry in connection.log if entry[0] == "execute"]


def test_the_patient_is_fixed_inside_the_same_transaction_before_any_select():
    connection = FakeConnection()

    SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    assert connection.log[0] == ("begin",)
    assert connection.log[1] == (
        "execute",
        "SELECT set_config('app.paciente_id', %s, true)",
        (str(PATIENT),),
    )
    assert connection.log[-1] == ("commit",)


def test_the_patient_id_is_a_parameter_never_part_of_the_sql_text():
    connection = FakeConnection()

    SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    for entry in connection.log:
        if entry[0] == "execute":
            assert str(PATIENT) not in entry[1]


def test_an_inactive_account_gets_no_results():
    connection = FakeConnection(account_row=("BLOQUEADO",), order_rows=[_row()])

    with pytest.raises(AccountNotActive):
        SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    assert not any("replica.ordenes_laboratorio" in sql for sql in _sql(connection))


def test_a_missing_account_fails_closed():
    connection = FakeConnection(account_row=None, order_rows=[_row()])

    with pytest.raises(AccountNotActive):
        SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    assert not any("replica.ordenes_laboratorio" in sql for sql in _sql(connection))


def test_the_account_is_checked_inside_the_same_transaction():
    connection = FakeConnection()

    SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    sql = _sql(connection)
    assert any("replica.cuentas_acceso" in statement for statement in sql)
    assert connection.log[0] == ("begin",)
    assert connection.log[-1] == ("commit",)


def test_rows_are_grouped_into_orders_with_their_validated_results():
    connection = FakeConnection(
        order_rows=[_row(), _row(result_id=UUID(int=7), analito="HbA1c")]
    )

    result = SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    assert result.patient_id == PATIENT
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["id"] == str(ORDER)
    assert [item["analito"] for item in order["resultados"]] == ["Glucosa", "HbA1c"]


def test_an_order_without_results_still_comes_back_with_an_empty_list():
    row = (ORDER, NOW, "SOLICITADA", DETAIL, "PENDIENTE",
           None, None, None, None, None, None, None, None, None)
    connection = FakeConnection(order_rows=[row])

    result = SupabaseReplicaReader(lambda: connection).results_of(PATIENT, USER)

    assert result.orders[0]["resultados"] == []

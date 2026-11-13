from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from medicalbooking.modules.replica.infrastructure.supabase_reader import SupabaseReplicaReader

PATIENT = UUID("11111111-1111-1111-1111-111111111111")
ORDER = UUID("44444444-4444-4444-4444-444444444444")
DETAIL = UUID("55555555-5555-5555-5555-555555555555")
RESULT = UUID("66666666-6666-6666-6666-666666666666")
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, log: list, rows: list) -> None:
        self._log = log
        self._rows = rows

    def execute(self, sql: str, params=None) -> None:
        self._log.append(("execute", " ".join(sql.split()), params))

    def fetchall(self) -> list:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list) -> None:
        self.log: list = []
        self._rows = rows

    @contextmanager
    def transaction(self):
        self.log.append(("begin",))
        yield
        self.log.append(("commit",))

    @contextmanager
    def cursor(self):
        yield FakeCursor(self.log, self._rows)


def _row(result_id=RESULT, analito="Glucosa"):
    return (
        ORDER, NOW, "COMPLETADA",
        DETAIL, "COMPLETADO",
        result_id, analito, "95", 95.0, "mg/dL", 70.0, 99.0, "NORMAL", NOW,
    )


def test_the_patient_is_fixed_inside_the_same_transaction_before_any_select():
    connection = FakeConnection(rows=[])

    SupabaseReplicaReader(lambda: connection).results_of(PATIENT)

    assert connection.log[0] == ("begin",)
    assert connection.log[1] == (
        "execute",
        "SELECT set_config('app.paciente_id', %s, true)",
        (str(PATIENT),),
    )
    selects = [entry for entry in connection.log if entry[0] == "execute"][1:]
    assert selects, "the replica must be queried"
    assert all("SELECT" in sql for _, sql, _ in selects)
    assert connection.log[-1] == ("commit",)


def test_the_patient_id_is_a_parameter_never_part_of_the_sql_text():
    connection = FakeConnection(rows=[])

    SupabaseReplicaReader(lambda: connection).results_of(PATIENT)

    for entry in connection.log:
        if entry[0] == "execute":
            assert str(PATIENT) not in entry[1]


def test_rows_are_grouped_into_orders_with_their_validated_results():
    connection = FakeConnection(rows=[_row(), _row(result_id=UUID(int=7), analito="HbA1c")])

    result = SupabaseReplicaReader(lambda: connection).results_of(PATIENT)

    assert result.patient_id == PATIENT
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["id"] == str(ORDER)
    assert [item["analito"] for item in order["resultados"]] == ["Glucosa", "HbA1c"]


def test_an_order_without_results_still_comes_back_with_an_empty_list():
    row = (ORDER, NOW, "SOLICITADA", DETAIL, "PENDIENTE",
           None, None, None, None, None, None, None, None, None)
    connection = FakeConnection(rows=[row])

    result = SupabaseReplicaReader(lambda: connection).results_of(PATIENT)

    assert result.orders[0]["resultados"] == []

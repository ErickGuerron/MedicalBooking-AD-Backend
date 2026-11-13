"""Supabase replica reader for fallback reads (spec 8.3, S8/S9).

Runs as replica_reader. The patient is fixed for the transaction with SET LOCAL, so the
row-level security policies of the replica only expose that patient's rows. The patient
id always travels as a bound parameter, never inside the SQL text. Without the SET LOCAL
the policies return no rows (fail closed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from medicalbooking.modules.replica.application.fallback_read import (
    AccountNotActive,
    PatientResults,
)

_ACCOUNT_STATE = "SELECT estado FROM replica.cuentas_acceso WHERE usuario_id = %s"

_ORDERS_WITH_RESULTS = """
SELECT o.id, o.fecha_orden, o.estado,
       d.id, d.estado,
       r.id, r.nombre_analito, r.valor_texto, r.valor_numerico, r.unidad,
       r.referencia_min, r.referencia_max, r.interpretacion, r.validado_en
FROM replica.ordenes_laboratorio o
LEFT JOIN replica.ordenes_laboratorio_detalle d ON d.orden_id = o.id
LEFT JOIN replica.resultados_laboratorio r ON r.detalle_orden_id = d.id
WHERE o.paciente_id = %s
ORDER BY o.fecha_orden, o.id, r.nombre_analito
"""


class SupabaseReplicaReader:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults:
        connection = self._connect()
        with connection.transaction():
            with connection.cursor() as cursor:
                # set_config(..., true) is SET LOCAL that accepts a bound parameter;
                # SET itself rejects placeholders in PostgreSQL.
                cursor.execute(
                    "SELECT set_config('app.paciente_id', %s, true)", (str(patient_id),)
                )
                cursor.execute(_ACCOUNT_STATE, (str(usuario_id),))
                account = cursor.fetchone()
                if account is None or account[0] != "ACTIVO":
                    # Raised inside the transaction: nothing from the replica is returned.
                    raise AccountNotActive("account not active in the replica")
                cursor.execute(_ORDERS_WITH_RESULTS, (str(patient_id),))
                rows = cursor.fetchall()
        return PatientResults(patient_id=patient_id, orders=_group(rows))


def _group(rows: list[tuple]) -> tuple[dict[str, Any], ...]:
    orders: dict[str, dict[str, Any]] = {}
    for (order_id, fecha, estado, _detail_id, _detail_estado,
         result_id, analito, valor_texto, valor_numerico, unidad,
         ref_min, ref_max, interpretacion, validado_en) in rows:
        key = str(order_id)
        order = orders.setdefault(
            key,
            {"id": key, "fecha_orden": fecha, "estado": estado, "resultados": []},
        )
        if result_id is not None:
            order["resultados"].append(
                {
                    "id": str(result_id),
                    "analito": analito,
                    "valor_texto": valor_texto,
                    "valor_numerico": valor_numerico,
                    "unidad": unidad,
                    "referencia_min": ref_min,
                    "referencia_max": ref_max,
                    "interpretacion": interpretacion,
                    "validado_en": validado_en,
                }
            )
    return tuple(orders.values())

"""Supabase replica reader for fallback reads (spec 8.3, S8/S9).

Runs as replica_reader. The rule mirrors the SQL authorization (identity load_access and
laboratory _can_read_order): the account is ACTIVO, the account holds the own-results
permission through its roles, and the patient belongs to that account. Only reviewed
proposals (ACEPTADA, CORREGIDA) are returned. Both ids and the permission code travel as
bound parameters, never inside the SQL text. The checks run inside one transaction, and
any failed check raises before the results are read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from medicalbooking.modules.replica.application.fallback_read import (
    AccessDenied,
    AccountNotActive,
    PatientResults,
)

OWN_RESULTS_PERMISSION = "resultados.leer_propios"

_ACCOUNT_STATE = "SELECT estado FROM replica.usuarios WHERE id = %s"

_HAS_OWN_RESULTS_PERMISSION = """
SELECT 1
FROM replica.usuarios_roles ur
JOIN replica.roles_permisos rp ON rp.rol_id = ur.rol_id
JOIN replica.permisos p ON p.id = rp.permiso_id
WHERE ur.usuario_id = %s AND p.codigo = %s
LIMIT 1
"""

_PATIENT_OF_ACCOUNT = "SELECT 1 FROM replica.pacientes WHERE id = %s AND usuario_id = %s"

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

# Only reviewed proposals exist in the replica; the state filter repeats the rule.
_REVIEWED_PROPOSALS = """
SELECT pc.orden_id, pc.estado, pc.resumen_propuesto, pc.conclusion_medica,
       ph.tipo, ph.explicacion, ph.regla_version
FROM replica.propuestas_clinicas pc
LEFT JOIN replica.propuestas_hallazgos ph ON ph.propuesta_id = pc.id
WHERE pc.estado IN ('ACEPTADA', 'CORREGIDA')
  AND pc.orden_id IN (SELECT id FROM replica.ordenes_laboratorio WHERE paciente_id = %s)
ORDER BY pc.orden_id, ph.tipo
"""


_PATIENT_OF_USER = "SELECT id FROM replica.pacientes WHERE usuario_id = %s LIMIT 1"


def _authorize(cursor: Any, usuario_id: UUID) -> None:
    """The rule shared by both reads: an active account holding the own-results permission."""
    cursor.execute("SELECT set_config('app.usuario_id', %s, true)", (str(usuario_id),))
    cursor.execute(_ACCOUNT_STATE, (str(usuario_id),))
    account = cursor.fetchone()
    if account is None or account[0] != "ACTIVO":
        raise AccountNotActive("account not active in the replica")
    cursor.execute(_HAS_OWN_RESULTS_PERMISSION, (str(usuario_id), OWN_RESULTS_PERMISSION))
    if cursor.fetchone() is None:
        raise AccessDenied("own-results permission missing")


class SupabaseReplicaReader:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    def patient_for(self, usuario_id: UUID) -> UUID:
        """The patient of an authorized account. Raises before any patient data is read."""
        connection = self._connect()
        with connection.transaction():
            with connection.cursor() as cursor:
                _authorize(cursor, usuario_id)
                cursor.execute(_PATIENT_OF_USER, (str(usuario_id),))
                row = cursor.fetchone()
        if row is None:
            raise AccessDenied("no patient belongs to the account")
        return UUID(str(row[0]))

    def results_of(self, patient_id: UUID, usuario_id: UUID) -> PatientResults:
        connection = self._connect()
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.paciente_id', %s, true)", (str(patient_id),))
                _authorize(cursor, usuario_id)
                cursor.execute(_PATIENT_OF_ACCOUNT, (str(patient_id), str(usuario_id)))
                if cursor.fetchone() is None:
                    raise AccessDenied("patient does not belong to the account")
                cursor.execute(_ORDERS_WITH_RESULTS, (str(patient_id),))
                rows = cursor.fetchall()
                cursor.execute(_REVIEWED_PROPOSALS, (str(patient_id),))
                proposal_rows = cursor.fetchall()
        return PatientResults(
            patient_id=patient_id,
            orders=_attach_diagnosis(_group(rows), proposal_rows),
        )

def _group(rows: list[tuple]) -> dict[str, dict[str, Any]]:
    orders: dict[str, dict[str, Any]] = {}
    for (
        order_id,
        fecha,
        estado,
        _detail_id,
        _detail_estado,
        result_id,
        analito,
        valor_texto,
        valor_numerico,
        unidad,
        ref_min,
        ref_max,
        interpretacion,
        validado_en,
    ) in rows:
        key = str(order_id)
        order = orders.setdefault(
            key,
            {
                "id": key,
                "fecha_orden": fecha,
                "estado": estado,
                "resultados": [],
                "diagnostico": None,
            },
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
    return orders


def _attach_diagnosis(orders: dict[str, dict[str, Any]], proposal_rows: list[tuple]) -> tuple:
    for order_id, estado, resumen, conclusion, tipo, explicacion, _version in proposal_rows:
        order = orders.get(str(order_id))
        if order is None:
            continue
        diagnosis = order["diagnostico"] or {
            "estado": estado,
            # CORREGIDA: the physician's own text.
            # ACEPTADA: the machine text the physician accepted.
            "conclusion": conclusion if estado == "CORREGIDA" else resumen,
            "hallazgos": [],
        }
        if tipo is not None:
            diagnosis["hallazgos"].append({"tipo": tipo, "explicacion": explicacion})
        order["diagnostico"] = diagnosis
    return tuple(orders.values())

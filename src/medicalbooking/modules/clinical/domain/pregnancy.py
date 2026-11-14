"""Pregnancy state of one order, read from its lab results (clinical findings S18). Pure function.

Each result may carry the state recorded at sample time (NO, T1, T2, T3). One agreed non-null value
is used. No value, or conflicting values, mean unknown (None).
"""

from __future__ import annotations

from collections.abc import Iterable


def estado_embarazo_del_pedido(valores: Iterable[str | None]) -> str | None:
    declarados = {valor for valor in valores if valor is not None}
    if len(declarados) == 1:
        return declarados.pop()
    return None

"""Pregnancy state of one order, read from its lab results (clinical findings S18).

One agreed non-null value is used. No value, or conflicting values, mean unknown (None), so the
iron and anaemia findings stay not evaluable for women of reproductive age (S5, S3).
"""

from __future__ import annotations

import pytest

from medicalbooking.modules.clinical.domain.pregnancy import estado_embarazo_del_pedido


@pytest.mark.parametrize(
    ("valores", "esperado"),
    [
        ([], None),
        ([None, None], None),
        (["T1"], "T1"),
        (["NO", None], "NO"),
        (["T2", "T2"], "T2"),
        (["T1", "T2"], None),
        (["NO", "T3", None], None),
    ],
)
def test_one_agreed_value_is_used_and_anything_else_is_unknown(valores, esperado) -> None:
    assert estado_embarazo_del_pedido(valores) == esperado

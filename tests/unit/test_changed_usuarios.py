"""Watermark user reader: payload matches the replica projection, no database needed."""

from __future__ import annotations

from typing import Any

import pytest

from medicalbooking.modules.replica.application.projection_consumer import _HANDLERS
from medicalbooking.modules.synchronization.infrastructure.changed_usuarios import (
    UsuarioChange,
    usuario_payload,
)
from medicalbooking.modules.synchronization.infrastructure.external_usuario_publisher import (
    SqlUsuarioOutboxPublisher,
)

_USUARIO_ID = "9B1E2C4A-0000-4000-8000-000000000010"
_PACIENTE_ID = "9B1E2C4A-0000-4000-8000-000000000011"


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "version": 42,
        "id": _USUARIO_ID,
        "estado": "ACTIVO",
        "nombre": "Ana",
        "apellido": "Lopez",
        "paciente_id": _PACIENTE_ID,
    }
    row.update(overrides)
    return row


class _RecordingTx:
    """Stands in for the replica transaction and records what the projection asks for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def upsert_account(self, usuario_id: str, estado: str) -> None:
        self.calls.append(("upsert_account", (usuario_id, estado)))

    def replace_roles(self, usuario_id: str, roles: list[int]) -> None:
        self.calls.append(("replace_roles", (usuario_id, roles)))

    def upsert_patient(self, id: str, usuario_id: str, nombre: str, apellido: str) -> None:
        self.calls.append(("upsert_patient", (id, usuario_id, nombre, apellido)))


def test_payload_carries_account_roles_and_patient_identity() -> None:
    payload = usuario_payload(_row(), roles=[3, 1])

    assert payload == {
        "usuario": {"id": _USUARIO_ID.lower(), "estado": "ACTIVO"},
        "roles": [1, 3],
        "paciente": {
            "id": _PACIENTE_ID.lower(),
            "usuario_id": _USUARIO_ID.lower(),
            "nombre": "Ana",
            "apellido": "Lopez",
        },
    }


def test_a_user_without_patient_profile_has_no_patient() -> None:
    payload = usuario_payload(_row(paciente_id=None), roles=[2])

    assert payload["paciente"] is None
    assert payload["roles"] == [2]


def test_a_user_without_roles_gets_an_empty_list() -> None:
    assert usuario_payload(_row(), roles=[])["roles"] == []


def test_the_projection_applies_the_payload_as_an_upsert() -> None:
    envelope = {"payload": usuario_payload(_row(), roles=[1])}
    tx = _RecordingTx()

    _HANDLERS["usuario.actualizado"](envelope, tx)

    assert tx.calls == [
        ("upsert_account", (_USUARIO_ID.lower(), "ACTIVO")),
        ("replace_roles", (_USUARIO_ID.lower(), [1])),
        ("upsert_patient", (_PACIENTE_ID.lower(), _USUARIO_ID.lower(), "Ana", "Lopez")),
    ]


def test_a_user_without_patient_only_updates_account_and_roles() -> None:
    envelope = {"payload": usuario_payload(_row(paciente_id=None), roles=[1])}
    tx = _RecordingTx()

    _HANDLERS["usuario.actualizado"](envelope, tx)

    assert [name for name, _ in tx.calls] == ["upsert_account", "replace_roles"]


def test_the_publisher_refuses_any_table_but_usuarios() -> None:
    publisher = SqlUsuarioOutboxPublisher(engine=None)  # type: ignore[arg-type]
    change = UsuarioChange(version=1, payload=usuario_payload(_row(), roles=[]))

    with pytest.raises(ValueError, match="no user publisher"):
        publisher.publish("citas", [change], 1)

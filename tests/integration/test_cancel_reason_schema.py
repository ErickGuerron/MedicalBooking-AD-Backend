"""The database itself refuses an unknown cancellation reason, and holds 1500 characters.

Migration 0026: the code is checked in SQL, not only by the application, and the text
column is nvarchar(1500).
"""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration.test_refund_request_api import _seed

pytestmark = pytest.mark.integration


def test_sql_refuses_a_reason_code_outside_the_closed_set(migrated_engine: Engine) -> None:
    seed = _seed(migrated_engine)
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE citas SET motivo_cancelacion_codigo = 'PORQUE_SI' WHERE id = :c"
                ),
                {"c": seed.cita_id},
            )


def test_sql_accepts_a_reason_code_from_the_closed_set(migrated_engine: Engine) -> None:
    seed = _seed(migrated_engine)
    with migrated_engine.begin() as conn:
        conn.execute(
            text("UPDATE citas SET motivo_cancelacion_codigo = 'IMPREVISTO_MEDICO' WHERE id = :c"),
            {"c": seed.cita_id},
        )


def test_the_reason_text_column_holds_1500_characters(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        length = conn.execute(
            text(
                "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'citas' AND COLUMN_NAME = 'motivo_cancelacion_texto'"
            )
        ).scalar_one()
    assert length == 1500
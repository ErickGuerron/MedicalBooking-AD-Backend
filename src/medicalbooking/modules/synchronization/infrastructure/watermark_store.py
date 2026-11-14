"""SQL Server adapter for the per-table sync watermark (last delivered rowversion as BIGINT).

The advance is monotonic: a MERGE only raises the stored version, so a retried or overlapping
run never moves the watermark backwards.
"""

from __future__ import annotations

from sqlalchemy import Connection, Engine, text

from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

_GET = text("SELECT ultima_version FROM sincronizacion_watermarks WHERE tabla = :tabla")

_ADVANCE = text(
    "MERGE sincronizacion_watermarks WITH (HOLDLOCK) AS target "
    "USING (VALUES (:tabla, CAST(:version AS bigint))) AS source (tabla, version) "
    "ON target.tabla = source.tabla "
    "WHEN MATCHED AND target.ultima_version < source.version THEN "
    "  UPDATE SET ultima_version = source.version, actualizado_en = SYSUTCDATETIME() "
    "WHEN NOT MATCHED THEN "
    "  INSERT (tabla, ultima_version) VALUES (source.tabla, source.version);"
)


class SqlWatermarkStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, table: str) -> int:
        """Return the last delivered version, or 0 when the table has never been synced."""
        with self._engine.connect() as conn:
            value = conn.execute(_GET, {"tabla": table}).scalar_one_or_none()
        return int(value) if value is not None else 0

    def advance(self, table: str, version: int) -> None:
        """Raise the watermark to ``version``. A lower or equal version changes nothing."""
        _check_version(version)

        def work() -> None:
            with self._engine.begin() as conn:
                advance_in(conn, table, version)

        retry_on_deadlock(work)


def _check_version(version: int) -> None:
    if version < 0:
        raise ValueError("watermark version must be non-negative")


def advance_in(conn: Connection, table: str, version: int) -> None:
    """Raise the watermark inside the caller's transaction, so it commits with the covered work."""
    _check_version(version)
    conn.execute(_ADVANCE, {"tabla": table, "version": version})

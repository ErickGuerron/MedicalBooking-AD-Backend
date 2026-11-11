"""SQL Server adapter for the user-to-Stripe-customer mapping (stripe_clientes).

Parameterized SQL only. The table holds the Stripe customer id and nothing else: no card data.
"""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from medicalbooking.shared.infrastructure.sql_errors import is_unique_violation


class SqlStripeCustomerStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def find(self, usuario_id: str) -> str | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                text("SELECT stripe_customer_id FROM stripe_clientes WHERE usuario_id = :usuario"),
                {"usuario": usuario_id},
            ).scalar_one_or_none()
        return str(value) if value is not None else None

    def save(self, usuario_id: str, stripe_customer_id: str) -> str:
        """Store the mapping. A concurrent request that already stored one wins: its row is
        reread and returned instead of failing."""
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO stripe_clientes (usuario_id, stripe_customer_id, creado_en) "
                        "VALUES (:usuario, :cliente, SYSUTCDATETIME())"
                    ),
                    {"usuario": usuario_id, "cliente": stripe_customer_id},
                )
        except IntegrityError as error:
            if not is_unique_violation(str(error.orig)):
                raise
            existing = self.find(usuario_id)
            if existing is None:
                raise
            return existing
        return stripe_customer_id

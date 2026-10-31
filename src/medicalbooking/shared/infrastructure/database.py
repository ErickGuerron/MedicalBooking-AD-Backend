"""SQLAlchemy engine factory for SQL Server / Azure SQL (infrastructure adapter)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from medicalbooking.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

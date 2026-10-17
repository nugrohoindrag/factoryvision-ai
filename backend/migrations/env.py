"""Alembic environment for the fv dataset (BigQuery or DuckDB)."""

from __future__ import annotations

from alembic import context
from alembic.ddl.impl import DefaultImpl
from sqlalchemy import text

from app.config import settings
from app.db.engine import database_url, make_engine
from app.db.models import SCHEMA, Base


class DuckDBImpl(DefaultImpl):
    """duckdb_engine ships no Alembic implementation."""

    __dialect__ = "duckdb"
    transactional_ddl = True


def ensure_schema(connection) -> None:
    """BigQuery: create the dataset in the plant region. DuckDB: the file itself is the fv catalog."""
    if connection.dialect.name == "bigquery":
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA} OPTIONS(location='{settings.region}')"))


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        eng = make_engine(context.config.get_main_option("sqlalchemy.url") or database_url())
        try:
            with eng.connect() as conn:
                _run(conn)
        finally:
            eng.dispose()  # DuckDB refuses a read-only open while a writer connection is pooled
    else:
        _run(connection)


def _run(connection) -> None:
    ensure_schema(connection)
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

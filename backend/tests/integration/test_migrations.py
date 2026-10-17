"""Alembic migrations must build exactly the tables and column types of the SQLAlchemy models.

Compared through information_schema: duckdb_engine's column reflection is not compatible with
SQLAlchemy 2.1, so Alembic autogenerate compare cannot be used against DuckDB.
"""

from alembic import command
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Double, String, text

from app.db.build_local import alembic_config
from app.db.engine import make_engine
from app.db.models import Base

DUCK_TYPE = [(Boolean, "BOOLEAN"), (BigInteger, "BIGINT"), (Double, "DOUBLE"), (DateTime, "TIMESTAMP WITH TIME ZONE"), (Date, "DATE"), (String, "VARCHAR")]


def expected_type(sa_type) -> str:
    return next(name for cls, name in DUCK_TYPE if isinstance(sa_type, cls))


def columns(conn) -> dict:
    rows = conn.execute(text("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_name != 'alembic_version'"))
    out: dict = {}
    for t, c, d in rows:
        out.setdefault(t, {})[c] = d
    return out


def test_upgrade_matches_models_and_downgrade_is_clean(tmp_path):
    url = f"duckdb:///{(tmp_path / 'fv.duckdb').as_posix()}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    with eng.connect() as conn:
        actual = columns(conn)
    want = {t.name: {c.name: expected_type(c.type) for c in t.columns} for t in Base.metadata.sorted_tables}
    assert actual == want
    command.downgrade(cfg, "base")
    with eng.connect() as conn:
        assert columns(conn) == {}
    eng.dispose()

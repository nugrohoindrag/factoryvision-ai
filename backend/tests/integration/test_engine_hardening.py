from pathlib import Path

import pytest
from app.db.engine import make_engine
from app.db.models import TABLES
from sqlalchemy import text


def _raw(url: str, statement: str, **kwargs: bool) -> object:
    eng = make_engine(url, read_only=True, **kwargs)
    try:
        with eng.connect() as conn:
            return conn.execute(text(statement)).fetchone()
    finally:
        eng.dispose()


def test_the_query_engine_cannot_read_files(warehouse: Path) -> None:
    """read_csv reaches the filesystem from inside SQL.

    check_sql is the first layer and rejects it. This is the second: a
    statement that got past the guardrail, or that never saw it, still
    cannot open a file. Both are needed, because the guardrail inspects
    text and this inspects the engine.
    """
    url = f"duckdb:///{warehouse.as_posix()}"
    with pytest.raises(Exception, match=r"(?i)permission|external"):
        _raw(url, "SELECT column0 FROM read_csv('/etc/hostname', header=false)")


def test_the_build_engine_can_read_files(tmp_path: Path) -> None:
    """build_local.py loads the seed CSVs through a temp view. If it could
    not, just db-build would fail, so the exemption is tested rather than
    assumed.

    It uses its own file. DuckDB refuses two connections to the same file
    with different configuration, so opening the shared warehouse here with
    the exemption would fail for a reason unrelated to what is being tested.
    """
    scratch = tmp_path / "fv.duckdb"
    seed = tmp_path / "seed.csv"
    seed.write_text("a,b\n1,2\n")
    eng = make_engine(f"duckdb:///{scratch.as_posix()}", allow_external_access=True)
    try:
        with eng.begin() as conn:
            conn.execute(text(f"CREATE TABLE t AS SELECT * FROM read_csv('{seed.as_posix()}', header=true)"))
            assert conn.execute(text("SELECT count(*) FROM t")).fetchone() is not None
    finally:
        eng.dispose()


def test_ordinary_reads_still_work_on_the_query_engine(warehouse: Path) -> None:
    eng = make_engine(f"duckdb:///{warehouse.as_posix()}", read_only=True)
    try:
        with eng.connect() as conn:
            for table in sorted(TABLES):
                assert conn.execute(text(f"SELECT count(*) FROM fv.{table}")).fetchone() is not None
    finally:
        eng.dispose()


def test_the_setting_actually_reached_the_engine(warehouse: Path) -> None:
    """Asserts the configuration landed on the connection rather than that
    the constructor was called with the right argument."""
    eng = make_engine(f"duckdb:///{warehouse.as_posix()}", read_only=True)
    try:
        with eng.connect() as conn:
            row = conn.exec_driver_sql("SELECT current_setting('enable_external_access')").fetchone()
        assert row is not None
        assert str(row[0]).lower() in {"false", "0"}
    finally:
        eng.dispose()

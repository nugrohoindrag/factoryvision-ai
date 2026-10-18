from __future__ import annotations

import copy
import threading
import time
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlglot import exp
from sqlglot.errors import ParseError

from app.config import settings
from app.db.engine import engine
from app.db.models import TABLES
from app.errors import FVError

ALLOWED_TABLES = set(TABLES)
MAX_ROWS = 5000

SQL_REJECTED = "FV-SQL-001"
SQL_NOT_ALLOWLISTED = "FV-SQL-002"
DATA_WAREHOUSE = "FV-DATA-002"

BQML_FORECAST_SQL = """
SELECT DATE(forecast_timestamp) AS d, forecast_value AS q
FROM ML.FORECAST(MODEL `fv.demand_arima`, STRUCT(CAST(:horizon AS INT64) AS horizon))
WHERE sku = :sku ORDER BY d
"""

_WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Merge,
    exp.TruncateTable,
    exp.Command,
)


def _referenced_tables(tree: exp.Expression) -> tuple[set[str], set[str]]:
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    names = set()
    functions = set()
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Subquery):
            continue
        if not isinstance(table.this, exp.Identifier):
            functions.add(table.sql(dialect="duckdb")[:60])
            continue
        if table.name and table.name not in cte_names:
            names.add(table.name)
    return names, functions


def check_sql(sql: str) -> str:
    s = sql.strip().rstrip(";")
    try:
        parsed = sqlglot.parse(s)
    except ParseError as e:
        raise FVError(SQL_REJECTED, "query could not be parsed", detail=str(e)[:200]) from e
    if len(parsed) != 1 or not isinstance(parsed[0], exp.Select):
        raise FVError(SQL_REJECTED, "one SELECT statement only")
    root = parsed[0]
    if root.find(*_WRITE_NODES) is not None:
        raise FVError(SQL_REJECTED, "statement contains a write")
    tables, functions = _referenced_tables(root)
    if functions:
        raise FVError(SQL_NOT_ALLOWLISTED, f"table functions are not allowlisted: {', '.join(sorted(functions))}")
    if not tables:
        raise FVError(SQL_REJECTED, "query must read fv.<table>")
    bad = {t for t in tables if t not in ALLOWED_TABLES}
    if bad:
        raise FVError(SQL_NOT_ALLOWLISTED, f"table not allowlisted: {', '.join(sorted(bad))}")
    if root.args.get("limit") is None:
        s = f"{s}\nLIMIT {MAX_ROWS}"
    return s


def _run(sql: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    try:
        with engine().connect() as conn:
            return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]
    except OperationalError as e:
        raise FVError(DATA_WAREHOUSE, detail=str(e.orig)[:300]) from e


# Each BigQuery query costs ~1-2 s of job round trip and a page runs several, while the fv tables are a read-only
# seed. Results are kept for CACHE_TTL_S per (sql, params); fv.plant_events, which Pub/Sub keeps writing, is never cached.
CACHE_TTL_S = 600
_CACHE_MAX = 1024
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _cached(sql: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not is_bigquery() or "plant_events" in sql:
        return _run(sql, params)
    key = (sql, repr(sorted((params or {}).items())))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_S:
        return copy.deepcopy(hit[1])
    rows = _run(sql, params)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now, rows)
    return copy.deepcopy(rows)


def query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return _cached(check_sql(sql), params)


def bqml_forecast(sku: str, horizon: int) -> list[dict[str, Any]]:
    return _cached(BQML_FORECAST_SQL, {"sku": sku, "horizon": horizon})


def is_bigquery() -> bool:
    return settings.warehouse == "bigquery"

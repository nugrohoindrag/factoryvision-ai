from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db.engine import engine
from app.db.models import TABLES
from app.errors import FVError

ALLOWED_TABLES = set(TABLES)
MAX_ROWS = 5000
_FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant|call|export|load|copy|attach|pragma|install)\b", re.I)
_TABLE_REF = re.compile(r"\bfv\.([a-z_]+)\b")


def check_sql(sql: str) -> str:
    s = sql.strip().rstrip(";")
    if ";" in s:
        raise FVError("FV-SQL-001", "one statement only")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise FVError("FV-SQL-001", "only SELECT queries are allowed")
    if _FORBIDDEN.search(s):
        raise FVError("FV-SQL-001", "statement contains a forbidden keyword")
    tables = set(_TABLE_REF.findall(s))
    if not tables:
        raise FVError("FV-SQL-001", "query must read fv.<table>")
    bad = tables - ALLOWED_TABLES
    if bad:
        raise FVError("FV-SQL-002", f"table not allowlisted: {', '.join(sorted(bad))}")
    if not re.search(r"\blimit\s+\d+\s*$", s, re.I):
        s = f"{s}\nLIMIT {MAX_ROWS}"
    return s


def _run(sql: str, params: dict | None) -> list[dict]:
    try:
        with engine().connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]
    except OperationalError as e:
        raise FVError("FV-DATA-002", detail=str(e.orig)[:300]) from e


def query(sql: str, params: dict | None = None) -> list[dict]:
    return _run(check_sql(sql), params)


def raw(sql: str, params: dict | None = None) -> list[dict]:
    return _run(sql, params)


def is_bigquery() -> bool:
    return settings.warehouse == "bigquery"

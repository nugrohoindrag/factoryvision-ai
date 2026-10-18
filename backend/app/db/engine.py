from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from app.config import ROOT, settings

LOCAL_DB = Path(os.environ.get("FV_LOCAL_DB", str(ROOT / "data" / "local" / "fv.duckdb")))


def database_url(warehouse: str | None = None, local_db: Path | None = None) -> str:
    url = os.environ.get("FV_DATABASE_URL")
    if url:
        return url
    if (warehouse or settings.warehouse) == "bigquery":
        return f"bigquery://{settings.project}/{settings.dataset}?location={settings.region}"
    return f"duckdb:///{(local_db or LOCAL_DB).as_posix()}"


def make_engine(url: str, *, read_only: bool = False, allow_external_access: bool = False) -> Engine:
    """allow_external_access exists for exactly one caller: the local build.

    Everything else reads the warehouse, and nothing that reads the warehouse
    needs to open a file. DuckDB's read_csv, read_parquet and read_text reach
    the filesystem from inside SQL, which is how a query guardrail can be
    bypassed at the engine level no matter what it inspects. Turning external
    access off closes that, and it is the second layer: check_sql is the
    first, and build_local.py is the only path that legitimately needs this.
    """
    if not url.startswith("duckdb"):
        return create_engine(url)
    connect_args: dict[str, Any] = {}
    if not allow_external_access:
        connect_args["config"] = {"enable_external_access": "false"}
    if read_only:
        connect_args["read_only"] = True
    return create_engine(url, connect_args=connect_args)


@lru_cache(maxsize=1)
def engine() -> Engine:
    return make_engine(database_url(), read_only=settings.warehouse != "bigquery")

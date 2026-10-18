from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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


def make_engine(url: str, read_only: bool = False) -> Engine:
    if url.startswith("duckdb"):
        return create_engine(url, connect_args={"read_only": read_only} if read_only else {})
    return create_engine(url)


@lru_cache(maxsize=1)
def engine() -> Engine:
    return make_engine(database_url(), read_only=settings.warehouse != "bigquery")

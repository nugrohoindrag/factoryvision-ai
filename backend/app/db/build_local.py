"""Build the local DuckDB warehouse: Alembic upgrade, then load the seed CSVs.

Run (from backend/): python -m app.db.build_local [--seed-dir ../data/seed] [--db ../data/local/fv.duckdb]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import ROOT, settings
from app.db.engine import LOCAL_DB, make_engine
from app.db.models import Base

BACKEND = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def build(seed_dir: Path, db_path: Path) -> Path:
    if db_path.stem != "fv":
        raise ValueError("the local warehouse file must be named fv.duckdb so that fv.<table> resolves")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    url = f"duckdb:///{db_path.as_posix()}"
    command.upgrade(alembic_config(url), "head")
    eng = make_engine(url)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            csv = seed_dir / f"{table.name}.csv"
            cols = ", ".join(c.name for c in table.columns)
            conn.execute(text(f"INSERT INTO {table.name} ({cols}) SELECT {cols} FROM read_csv('{csv.as_posix()}', header=true, nullstr='', all_varchar=true)"))
    eng.dispose()
    return db_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=str(settings.seed_dir))
    ap.add_argument("--db", default=str(LOCAL_DB))
    a = ap.parse_args()
    print("built", build(Path(a.seed_dir), Path(a.db)).relative_to(ROOT))


if __name__ == "__main__":
    main()

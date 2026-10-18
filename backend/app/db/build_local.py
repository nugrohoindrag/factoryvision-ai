from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.config import ROOT, settings
from app.db.engine import LOCAL_DB, make_engine
from app.db.models import Base

BACKEND = Path(__file__).resolve().parents[2]

SET_SEED_PATH = "SET VARIABLE seed_path = :p"
CREATE_SEED_VIEW = "CREATE OR REPLACE TEMP VIEW fv_seed AS SELECT * FROM read_csv(getvariable('seed_path'), header=true, nullstr='', all_varchar=true)"


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def build(seed_dir: Path, db_path: Path) -> Path:
    msg = "the local warehouse file must be named fv.duckdb so that fv.<table> resolves"
    if db_path.stem != "fv":
        raise ValueError(msg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    url = f"duckdb:///{db_path.as_posix()}"
    command.upgrade(alembic_config(url), "head")
    eng = make_engine(url)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            csv = seed_dir / f"{table.name}.csv"
            cols = [c.name for c in table.columns]
            conn.execute(text(SET_SEED_PATH), {"p": csv.as_posix()})
            conn.execute(text(CREATE_SEED_VIEW))
            source = sa.table("fv_seed", *(sa.column(c) for c in cols))
            target = sa.table(table.name, *(sa.column(c) for c in cols))
            conn.execute(target.insert().from_select(cols, sa.select(*(sa.column(c) for c in cols)).select_from(source)))
    eng.dispose()
    return db_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=str(settings.seed_dir))
    ap.add_argument("--db", default=str(LOCAL_DB))
    a = ap.parse_args()
    sys.stdout.write(f"built {build(Path(a.seed_dir), Path(a.db)).relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()

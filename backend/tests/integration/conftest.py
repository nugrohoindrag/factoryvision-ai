from __future__ import annotations

import pytest

from app.db import build_local
from app.db import engine as db_engine
from simulator.seed.generator import Generator


@pytest.fixture(scope="session")
def seed_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed")
    g = Generator()
    g.run()
    g.write(out)
    return out


@pytest.fixture(scope="session", autouse=True)
def warehouse(seed_dir, tmp_path_factory):
    db = build_local.build(seed_dir, tmp_path_factory.mktemp("db") / "fv.duckdb")
    mp = pytest.MonkeyPatch()
    mp.setattr(db_engine, "LOCAL_DB", db)
    db_engine.engine.cache_clear()
    yield db
    db_engine.engine().dispose()
    db_engine.engine.cache_clear()
    mp.undo()

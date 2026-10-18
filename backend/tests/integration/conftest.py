from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.db import build_local
from app.db import engine as db_engine
from simulator.seed.generator import Generator

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(scope="session")
def seed_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out: Path = tmp_path_factory.mktemp("seed")
    g = Generator()
    g.run()
    g.write(out)
    return out


@pytest.fixture(scope="session", autouse=True)
def warehouse(seed_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    db = build_local.build(seed_dir, tmp_path_factory.mktemp("db") / "fv.duckdb")
    mp = pytest.MonkeyPatch()
    mp.setattr(db_engine, "LOCAL_DB", db)
    db_engine.engine.cache_clear()
    yield db
    db_engine.engine().dispose()
    db_engine.engine.cache_clear()
    mp.undo()

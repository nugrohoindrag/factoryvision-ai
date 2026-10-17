"""Test pyramid: tests/unit (no I/O), tests/integration (DuckDB + Alembic), tests/e2e (running API).

Markers are applied from the directory, so `pytest -m unit` runs only the base of the pyramid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items):
    for item in items:
        for layer in ("unit", "integration", "e2e"):
            if f"{Path('tests') / layer}" in str(item.path):
                item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    return json.loads((REPO / "evals" / "ground_truth.json").read_text())

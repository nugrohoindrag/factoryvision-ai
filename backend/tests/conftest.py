from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        for layer in ("unit", "integration", "e2e"):
            if f"{Path('tests') / layer}" in str(item.path):
                item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture(scope="session")
def ground_truth() -> dict[str, Any]:
    facts: dict[str, Any] = json.loads((REPO / "evals" / "ground_truth.json").read_text())
    return facts

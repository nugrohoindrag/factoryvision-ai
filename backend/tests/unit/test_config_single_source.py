import tomllib
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "pyproject.toml"
ROOT = BACKEND.parents[1] / "pyproject.toml"


def _tables(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_backend_pyproject_has_no_tool_tables() -> None:
    tool_tables = sorted(_tables(BACKEND).get("tool", {}))
    assert tool_tables == ["pytest"], tool_tables


def test_root_pyproject_owns_every_linter() -> None:
    tool = _tables(ROOT).get("tool", {})
    assert {"ruff", "mypy", "coverage"} <= set(tool)


def test_ruff_selects_the_declared_rules() -> None:
    select = _tables(ROOT)["tool"]["ruff"]["lint"]["select"]
    required = {
        "S",
        "BLE",
        "PGH",
        "RUF",
        "DTZ",
        "B",
        "I",
        "TC",
        "TRY",
        "ANN",
        "ASYNC",
        "EM",
        "TID",
        "UP",
        "C4",
        "PIE",
        "PERF",
        "RET",
        "SIM",
        "T20",
        "FA",
    }
    assert required <= set(select), required - set(select)

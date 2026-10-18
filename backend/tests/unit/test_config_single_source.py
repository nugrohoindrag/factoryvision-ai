import tomllib
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "pyproject.toml"
ROOT = BACKEND.parents[1] / "pyproject.toml"


def _tables(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _tool_tables(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        header = line.strip()
        if header.startswith("[tool.") and header.endswith("]"):
            names.append(header[1:-1])
    return names


def test_backend_pyproject_has_no_tool_tables() -> None:
    tool_tables = _tool_tables(BACKEND)
    assert tool_tables == ["tool.pytest.ini_options"], tool_tables


def test_root_pyproject_owns_every_linter() -> None:
    tool = _tables(ROOT).get("tool", {})
    assert {"ruff", "mypy", "coverage"} <= set(tool)


def test_ruff_selects_the_declared_rules() -> None:
    select = _tables(ROOT)["tool"]["ruff"]["lint"]["select"]
    assert {"S", "BLE", "PGH", "RUF", "DTZ", "B", "I"} <= set(select), select

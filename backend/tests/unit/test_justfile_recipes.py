import os
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JUSTFILE = ROOT / "justfile"

REQUIRED = {
    "bootstrap",
    "seed",
    "db-build",
    "test",
    "test-unit",
    "test-integration",
    "lint",
    "lint-fix",
    "typecheck",
    "ci-backend",
    "ci-frontend",
    "verify",
    "clean",
}


def _recipe_names() -> set[str]:
    text = JUSTFILE.read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z][a-z0-9-]*)(?:\s+[a-z][a-z0-9-]*)*:(?:\s.*)?$", text, re.MULTILINE))


def test_every_required_recipe_exists() -> None:
    assert _recipe_names() >= REQUIRED, REQUIRED - _recipe_names()


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed; CI installs it")
def test_recipes_are_reachable_with_just(monkeypatch: pytest.MonkeyPatch) -> None:
    just = shutil.which("just")
    assert just is not None, "just is not installed"
    monkeypatch.chdir(ROOT)
    pid = os.posix_spawn(just, [just, "--list", "--unsorted"], os.environ)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, f"just --list --unsorted failed in {ROOT}"

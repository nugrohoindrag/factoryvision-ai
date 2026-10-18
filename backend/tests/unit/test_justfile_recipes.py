import re
import shutil
import subprocess
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
def test_recipes_are_reachable_with_just() -> None:
    just = shutil.which("just")
    assert just is not None, "just is not installed"
    # subprocess, not os.posix_spawn: posix_spawn does not exist on Windows. The argv is fixed and runs without a shell.
    result = subprocess.run([just, "--list", "--unsorted"], cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, f"just --list --unsorted failed in {ROOT}: {result.stderr}"

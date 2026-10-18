import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CI = ROOT / ".github" / "workflows" / "ci.yml"
JUSTFILE = ROOT / "justfile"

# The tools whose invocation must mean the same thing locally and in CI. These
# are the ones where a divergence means CI is measuring something different
# from what a developer measures.
SHARED_TOOLS = ("ruff", "mypy", "pytest")

# ci.yml is parsed with a purpose-built scanner rather than a YAML library.
# PyYAML is only present transitively, so importing it in a unit test would
# make this gate depend on another package's dependency choices. The workflow
# is ours and has one shape per step: either `run:` or `uses:`.
_STEP = re.compile(r"^\s{6}- (?:run|uses):\s*(?P<body>.*)$")
_CWD = re.compile(r"^\s{8}working-directory:\s*(?P<dir>\S+)\s*$")
_JOB = re.compile(r"^  (?P<name>[a-z][a-z0-9-]*):\s*$")


def _normalise(command: str) -> str:
    command = command.replace("{{backend_dir}}", "backend").replace("{{frontend_dir}}", "frontend")
    command = re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", command)
    command = command.replace(" --silent", "")
    command = command.replace('"', "").replace("'", "")
    return " ".join(command.split())


def _ci_steps() -> list[tuple[str, str, str | None]]:
    steps: list[tuple[str, str, str | None]] = []
    job = ""
    run = cwd = None
    for line in CI.read_text(encoding="utf-8").splitlines():
        if job_match := _JOB.match(line):
            job = job_match["name"]
            continue
        if step_match := _STEP.match(line):
            if run is not None:
                steps.append((job, run, cwd))
            body = step_match["body"].strip().strip('"')
            run = body if body.startswith(("uv ", "npm ", "python ", "cd ")) else None
            cwd = None
            continue
        if run is not None and (cwd_match := _CWD.match(line)):
            cwd = cwd_match["dir"]
    if run is not None:
        steps.append((job, run, cwd))
    return steps


def _just_commands() -> list[str]:
    return [_normalise(line) for line in JUSTFILE.read_text(encoding="utf-8").splitlines() if "uv run" in line or "npm --prefix" in line]


def _ci_commands(tool: str) -> list[tuple[str, str]]:
    return [(job, _normalise(run)) for job, run, _ in _ci_steps() if tool in run]


def test_the_scanner_finds_the_ci_steps() -> None:
    """If this fails the other tests here are vacuously passing."""
    jobs = {job for job, _, _ in _ci_steps()}
    assert {"backend-lint", "backend-typecheck", "backend-test", "backend-contract", "frontend"} <= jobs


@pytest.mark.parametrize("tool", SHARED_TOOLS)
def test_every_ci_command_exists_in_a_just_recipe(tool: str) -> None:
    commands = _just_commands()
    missing = {f"{job}: {run}" for job, run in _ci_commands(tool) if not any(run in c for c in commands)}
    assert not missing, f"CI runs these but no just recipe does: {sorted(missing)}"


def test_ci_runs_pytest_from_the_directory_that_holds_the_pytest_config() -> None:
    """backend/pyproject.toml holds the only [tool.pytest.ini_options].

    Run pytest from anywhere else and no configfile is found: pythonpath stops
    applying, `app` is not importable, and -m unit silently stops filtering.
    That is a green job measuring nothing, so it is worth a gate of its own.
    """
    offenders = [f"{job}: {run}" for job, run, cwd in _ci_steps() if "pytest" in run and cwd != "backend" and not run.startswith("cd backend")]
    assert not offenders, f"pytest must run with backend/ as the working directory: {offenders}"


def test_ci_exports_the_environment_the_justfile_depends_on() -> None:
    """The justfile exports these; CI has to set them itself or it diverges.

    Matched as an assignment rather than a substring: the comment above the
    variable names it, so a substring check passes on a file where the setting
    has been deleted. That is the false green this repository exists to prevent,
    and it is the reason this gate reads the value and not the name.
    """
    text = CI.read_text(encoding="utf-8")
    assigned = re.search(r"^\s*UV_PROJECT_ENVIRONMENT:\s*(?P<value>.+?)\s*$", text, re.MULTILINE)
    assert assigned, "uv would resolve a second virtualenv under backend/"
    assert assigned["value"].startswith("${{"), assigned["value"]
    assert "--python 3.12" in text, "CI must pin the interpreter the justfile pins"

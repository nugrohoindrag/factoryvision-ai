set shell := ["bash", "-euo", "pipefail", "-c"]

# Must stay absolute. uv resolves a relative UV_PROJECT_ENVIRONMENT against the
# *project* directory, not the caller's cwd, so a bare `.venv` would create
# backend/.venv as a second, stale environment.
export UV_PROJECT_ENVIRONMENT := justfile_directory() / ".venv"

# coverage reads its config from the cwd only; every pytest run here happens in
# backend/, so point it back at the root pyproject.toml.
export COVERAGE_RCFILE := justfile_directory() / "pyproject.toml"

backend_dir := "backend"
frontend_dir := "frontend"

# List every recipe
_default:
    @just --list

# Install backend and frontend dependencies
bootstrap:
    uv sync --project {{backend_dir}}
    npm --prefix {{frontend_dir}} ci

# Generate 90 days of seed data -> data/seed
seed:
    cd {{backend_dir}} && uv run --project . --python 3.12 python -m simulator.seed.generator

# Migrate and load the local DuckDB warehouse -> data/local/fv.duckdb
db-build:
    cd {{backend_dir}} && uv run --project . --python 3.12 python -m app.db.build_local

# Run unit and integration tests
test: test-unit test-integration

# Run unit tests (pure logic, no I/O)
test-unit:
    cd {{backend_dir}} && uv run --project . --python 3.12 pytest -m unit

# Run integration tests (real DuckDB built through the migrations)
test-integration:
    cd {{backend_dir}} && uv run --project . --python 3.12 pytest -m integration

# Check formatting and lint rules (Python only)
lint:
    uv run --project {{backend_dir}} --python 3.12 ruff check .
    uv run --project {{backend_dir}} --python 3.12 ruff format --check .

# Fix what can be fixed automatically (Python only)
lint-fix:
    uv run --project {{backend_dir}} --python 3.12 ruff check --fix .
    uv run --project {{backend_dir}} --python 3.12 ruff format .

# Type-check the backend and the frontend
typecheck:
    uv run --project {{backend_dir}} --python 3.12 mypy
    npm --prefix {{frontend_dir}} run --silent typecheck

# Everything CI runs for the backend, measured once over both tiers. Depends on
# lint and typecheck, not on test: coverage has to be collected during the run,
# so running `test` first would measure a second, uninstrumented pass.
ci-backend: lint typecheck
    cd {{backend_dir}} && uv run --project . --python 3.12 pytest -m "unit or integration" --cov --cov-branch --cov-report=xml --cov-report=term

# Everything CI runs for the frontend
ci-frontend:
    npm --prefix {{frontend_dir}} run --silent build
    npm --prefix {{frontend_dir}} test

# Serve the API on :8080
serve:
    cd {{backend_dir}} && uv run --project . --python 3.12 uvicorn app.main:app --reload --port 8080

# Serve the frontend on :5173, proxying /api to :8080
serve-frontend:
    npm --prefix {{frontend_dir}} run dev

# Run the full CI surface locally. This is not all of CI: the Playwright smoke
# test and the error-catalog drift gate are not recipes, and ci-backend measures
# coverage that CI does not.
verify: ci-backend ci-frontend

# Regenerate docs/error-catalog.md from backend/app/errors.py
catalog:
    cd {{backend_dir}} && uv run --project . --python 3.12 python -m app.errors

# Remove generated data and coverage artefacts
clean:
    rm -rf data/seed data/local .coverage coverage.xml


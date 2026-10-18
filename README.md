# Factory Vision AI

Order-to-Delivery copilot for mid-sized manufacturers. Built for the Google Cloud AI Builder Cup 2026 —
Manufacturing: Intelligent Operations & Industrial Efficiency.

**This repository is early.** The data layer, the plant simulator, the domain tools and the test pyramid are
built and green. The agent layer and the product UI are not. The section [What is not built yet](#what-is-not-built-yet)
is the accurate part of this README; everything above it describes working code.

> Work in progress. All company, customer, vendor and car-model names are fictional.

## What is not built yet

None of the following exists in this repository. Do not read about them anywhere else in the tree and assume
otherwise:

- **The eight Google ADK agents.** `google-adk`, `google-genai`, Firestore, Pub/Sub and Cloud Storage are listed in
  `backend/pyproject.toml` under the `planned` dependency group, which is **not installed**. There is no agent code.
- **The 3D digital twin.** There is no React Three Fiber, no Three.js and no rendering code of any kind in `frontend/`.
- **Module pages and the Action Center.** `frontend/src/` is a shell with one component and one formatter module.
- **Vertex AI, Cloud Run, BigQuery ML or any Google Cloud deployment.** `backend/app/db/load_bigquery.py` implements a
  BigQuery loader that has never been run, since no cloud project or credentials exist. `infra/` is empty.

The API has exactly one endpoint: `GET /api/health`.

## Repository layout

| Path | Contents |
|---|---|
| `justfile` | Every command in the repo. Run `just --list`. |
| `pyproject.toml` | Tool configuration only: ruff, mypy, coverage. Not a Python project. |
| `backend/app/` | FastAPI service. `main.py` holds the app, the request-id middleware, the error handlers and `/api/health`. `errors.py` is the generated error catalog. `config.py` is the single source of truth for settings. |
| `backend/app/db/` | `models.py` is the SQLAlchemy definition of the `fv` dataset, `engine.py` picks DuckDB or BigQuery, `build_local.py` runs Alembic and loads the seed. |
| `backend/app/tools/` | Deterministic, tested domain functions — forecast, planning (CP-SAT), MES/OEE, material, purchasing, quality, delivery. Plain Python, no LLM. They read only through `app/warehouse.py`. |
| `backend/app/warehouse.py` | The SQL guardrail: one `SELECT`, `fv.`-prefixed allowlisted tables, mandatory `LIMIT`. |
| `backend/migrations/` | Alembic. One revision, `0001_initial_fv_schema.py`. |
| `backend/simulator/` | The Vidad plant spec (`plant.py`) and a deterministic seed generator producing 90 days of data. |
| `backend/tests/` | `unit/`, `integration/`, `e2e/`. Layer markers are assigned from the directory. |
| `frontend/` | React 19 + Vite + TypeScript shell that fetches `/api/health`. Vitest and Playwright tests. |
| `data/` | Generated. Git-ignored. `data/seed/` holds 25 table CSVs plus the seed manifest, `data/local/` holds `fv.duckdb`. |
| `evals/` | `ground_truth.json`: the seven planted data stories and their tolerances. |
| `docs/` | [testing.md](docs/testing.md) and [error-catalog.md](docs/error-catalog.md). The error catalog is generated. |
| `infra/` | Empty. |

## Commands

Every command in this repository is a `just` recipe. Do not `cd backend` and do not run `uv` or `npm` by hand —
the recipes pin the interpreter, the virtualenv location and the coverage config, and CI calls these same recipes.

```bash
just --list              # every recipe, with its description
just bootstrap           # uv sync the backend + npm ci the frontend
just seed                # 90 days of seed CSVs -> data/seed
just db-build            # Alembic upgrade + load -> data/local/fv.duckdb
just test                # unit + integration
just test-unit           # 45 selected, pure logic, no I/O
just test-integration    # 20 selected, real DuckDB built through the migrations
just lint                # ruff check + ruff format --check  (Python only)
just lint-fix            # ruff --fix + ruff format          (Python only)
just typecheck           # mypy (backend) + tsc (frontend)
just ci-backend          # lint, typecheck, tests, coverage
just ci-frontend         # vite build + vitest
just verify              # ci-backend + ci-frontend
just catalog             # regenerate docs/error-catalog.md from app/errors.py
just clean               # remove data/seed, data/local, coverage artefacts
```

There is one virtualenv, at the repository root. The justfile exports `UV_PROJECT_ENVIRONMENT` so that every `uv`
invocation uses it regardless of the working directory.

To serve the app after `just bootstrap && just seed && just db-build`:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8080
cd frontend && npm run dev    # http://localhost:5173, proxies /api to :8080
```

## Current state of the checks

`just lint` and `just typecheck` **do not pass today**. They are wired up and they fail loudly, which is the point:
CI calls them, so a command that only works on a laptop is not a command.

- `just lint` — ruff reports 126 findings across the backend, mostly missing annotations and import ordering.
- `just typecheck` — mypy aborts before checking anything: `backend/tests/` contains several `conftest.py` files and
  no `__init__.py`, so mypy reports `Duplicate module named "conftest"`. It also warns that the `pydantic-mypy`
  section is not a recognised option.

The tests are green: 45 unit and 20 integration pass. Do not silence the linters to make them pass.

## Data layer

The SQLAlchemy models in `backend/app/db/models.py` are the single schema definition of the `fv` dataset. Alembic
migrates it on **BigQuery** in the cloud (`sqlalchemy-bigquery`) and on **DuckDB** locally (`duckdb-engine`), so the
same SQL runs in both. The domain tools read only through `warehouse.query()`, which rejects anything that is not a
single allowlisted `SELECT` with a `LIMIT`. `backend/tests/integration/test_migrations.py` fails if a migration
produces anything other than the tables and column types the models declare.
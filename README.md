# Factory Vision AI

Order-to-Delivery copilot with a live digital twin for mid-sized manufacturers. Built for the Google Cloud AI Builder
Cup 2026 — Manufacturing: Intelligent Operations & Industrial Efficiency.

Eight Google ADK agents (Gemini on Vertex AI) connect seven modules on top of **Vidad**, a simulated car-bumper plant
(injection molding, painting, assembly). Every agent action is a proposal with its impact in Rupiah, scrap, kWh and
CO₂e; nothing changes until a person approves it, and then every module and the 3D twin react.

The section [What is not built yet](#what-is-not-built-yet) is the accurate list of gaps; everything else describes
working, tested code.

> All company, customer, vendor and car-model names are fictional.

## What is built

| Layer | What exists | Tested by |
|---|---|---|
| Data | `fv` dataset (SQLAlchemy models, Alembic for BigQuery and DuckDB), 90-day deterministic seed with seven planted stories | `test_migrations.py`, `test_stories.py` |
| Domain tools | Forecast, CP-SAT molding plan, OEE / Pareto / machine health, material projection, vendor scorecard, defect correlation, ATP, `impact_calculator` | `test_tools_contract.py`, `test_impact.py` |
| Human in the loop | Action Center: proposals `pending` → `approved` / `rejected` / `failed`, plan-level approve, decision trail; approved commands go to Pub/Sub `agent-commands` | `test_actions.py`, `test_api_platform.py` |
| Digital twin | Lazy simulator (`app/simulation/`): advances only on `POST /api/twin/tick`, per-session state rebuilt by replay, commands for maintenance, mold swap, lot hold, PO receipt | `test_simulator.py`, `test_api_twin.py` |
| Agents | Orchestrator + seven module agents (`app/agents/`), tools that can only propose, a timeline of every agent and tool call, SSE for live runs, Replay | `test_agents_plumbing.py` (scripted fake model), `test_replay.py` |
| Gemini functions | Twin Vision Analyzer (per-camera cache, debounce, per-session cap, accuracy score against the simulator), document extraction (customer PO PDF, vendor email), QC photo verification | `test_ai_functions.py` (Gemini mocked) |
| Frontend | Plant Briefing, seven module pages, Action Center, 3D twin in React Three Fiber with virtual cameras and the INS-01 conveyor | `npm test`, Playwright smoke |
| Cloud | `infra/setup.sh` (service accounts, Firestore, buckets, BigQuery + BQML, Pub/Sub with a BigQuery sink), `infra/deploy.sh` (Cloud Run), `Dockerfile` | Run by hand against the project on 18 Oct 2026, except `deploy.sh` |

## What is not built yet

- **No Cloud Run deployment.** `infra/deploy.sh` has never been run. BigQuery, BQML, Firestore and Pub/Sub exist in
  the project; the app has only run locally.
- **The shipped Replay is scripted.** `data/replays/disruption_response.json` was recorded with the deterministic
  playbook (`python -m app.agents.replay record --scripted`), not with Gemini. The UI labels it as such. A live
  recording is still to be made.
- **No live agent eval has run.** `tests/e2e/test_agents_live.py` exists; it has never been pointed at Vertex AI.
- **QC-5 uses REST sampling, not the Gemini Live API.** None of the configured models is served by the Live API
  (tested 18 Oct 2026), so CAM-Inspection sends each new bumper as one `generateContent` call, for at most 60 s.
- **QC photo verification misreads light colours** (C02 vs C04 in the one pair tested). Accuracy over the 95
  catalogued photos has not been measured.
- **No defect photos.** QC-1b (defect classification from real photos) has nothing to classify yet.
- **The BQML forecast misses the demand story.** ARIMA_PLUS over the full 90 days predicts +10% for FB-ARKA; the
  ground truth expects 30–40%. The local fallback forecaster does land in range.
- **No painting schedule** in the planner; CP-SAT plans molding only.
- **No demo login.** The backend verifies Firebase ID tokens when `FV_REQUIRE_AUTH=true`; there is no sign-in UI.
- **Photos are unreviewed.** All 95 entries in `data/photos/catalog.json` are `reviewed: false`; with
  `FV_PHOTOS_REQUIRE_REVIEW=true` (the deploy default) the conveyor and QC page show none.

## Repository layout

| Path | Contents |
|---|---|
| `justfile` | Every command in the repo. Run `just --list`. |
| `pyproject.toml` | Tool configuration only: ruff, mypy, coverage. Not a Python project. |
| `backend/app/` | FastAPI service. `main.py` holds the app, the middleware, the error handlers and the frontend fallback. `errors.py` is the generated error catalog. `config.py` is the single source of truth for settings. |
| `backend/app/plant.py` | The Vidad plant spec: machines, products, molds, materials, customers, vendors, defect and downtime taxonomies, cost constants. |
| `backend/app/db/` | `models.py` is the SQLAlchemy definition of the `fv` dataset, `engine.py` picks DuckDB or BigQuery, `build_local.py` runs Alembic and loads the seed, `load_bigquery.py` loads it into BigQuery. |
| `backend/app/tools/` | Deterministic domain functions, plus `impact.py`. Plain Python, no LLM. They read only through `app/warehouse.py`. |
| `backend/app/warehouse.py` | The SQL guardrail: one `SELECT`, `fv.`-prefixed allowlisted tables, mandatory `LIMIT`. |
| `backend/app/actions.py`, `effects.py`, `modules.py`, `briefing.py` | Action Center, what approved actions change in the module numbers, the module pages, the Plant Briefing. |
| `backend/app/simulation/`, `twin.py` | The lazy plant simulator and its per-session service. |
| `backend/app/agents/` | ADK agents, their tools, the run timeline, Replay. |
| `backend/app/ai/` | Single-call Gemini functions: `vision.py` (Twin Vision Analyzer), `extract.py` (documents and QC photos). |
| `backend/app/routes/` | HTTP routes; `main.py` includes them. |
| `backend/app/store.py`, `pubsub.py`, `session.py` | Firestore or in-memory store, Pub/Sub or in-process transport, session id and per-session caps. |
| `backend/migrations/` | Alembic. One revision, `0001_initial_fv_schema.py`. |
| `backend/simulator/` | Offline tools: the 90-day seed generator and the photo catalog builder. They import `app`; `app` never imports `simulator`. |
| `backend/tests/` | `unit/`, `integration/`, `e2e/`. Layer markers are assigned from the directory. |
| `frontend/` | React 19 + Vite + TypeScript: module pages, Action Center, 3D twin (`src/twin/`). Vitest and Playwright tests. |
| `data/` | `replays/` is tracked. `seed/`, `local/` and `photos/` are generated and git-ignored. |
| `evals/` | `ground_truth.json`: the seven planted data stories and their tolerances. |
| `docs/` | [testing.md](docs/testing.md) and [error-catalog.md](docs/error-catalog.md). The error catalog is generated. |
| `infra/` | `setup.sh`, `deploy.sh`, `bq_forecast.sql`. |

## Commands

Every command in this repository is a `just` recipe. Do not `cd backend` and do not run `uv` or `npm` by hand —
the recipes pin the interpreter, the virtualenv location and the coverage config. CI runs the same tools with the
same pins, but calls `uv` and `npm` directly rather than through `just`; the two surfaces are kept in step by hand,
not by a gate.

On Windows, run `just` from Git Bash. The justfile's shell is `bash`, and from PowerShell that name resolves to WSL,
which fails when no Linux distribution is installed.

```bash
just --list              # every recipe, with its description
just bootstrap           # uv sync the backend + npm ci the frontend
just seed                # 90 days of seed CSVs -> data/seed
just db-build            # Alembic upgrade + load -> data/local/fv.duckdb
just test                # unit + integration
just test-unit           # pure logic, no I/O
just test-integration    # real DuckDB built through the migrations
just lint                # ruff check + ruff format --check  (Python only)
just lint-fix            # ruff --fix + ruff format          (Python only)
just typecheck           # mypy (backend) + tsc (frontend)
just ci-backend          # lint, typecheck, tests, coverage
just ci-frontend         # vite build + vitest
just verify              # ci-backend + ci-frontend (excludes the e2e smoke and catalog drift)
just catalog             # regenerate docs/error-catalog.md from app/errors.py
just serve               # uvicorn on :8080
just serve-frontend      # vite on :5173
just clean               # remove data/seed, data/local, coverage artefacts
```

There is one virtualenv, at the repository root. The justfile exports `UV_PROJECT_ENVIRONMENT` so that every `uv`
invocation uses it regardless of the working directory.

To serve the app after `just bootstrap && just seed && just db-build`:

```bash
just serve           # uvicorn on http://localhost:8080; serves frontend/dist too once it is built
just serve-frontend  # vite on http://localhost:5173, proxies /api to :8080
```

Without `GOOGLE_CLOUD_PROJECT` the AI is off (`FV_AI=auto`) and the app runs in Replay mode. With a project and
Application Default Credentials (`gcloud auth application-default login`), Live mode calls Gemini on Vertex AI. No API
keys are used anywhere. The models are served from the `global` endpoint, so `FV_MODEL_LOCATION` and
`GOOGLE_CLOUD_LOCATION` (which ADK reads itself) default to `global`.

## Current state of the checks

- `just lint` — ruff check and ruff format --check, clean.
- `just typecheck` — mypy over the backend sources, and `tsc` for the frontend. Clean on Linux and Windows.
- `just test` — unit and integration. All pass on Linux and Windows. Two warnings remain in the summary, both
  third-party and allowlisted in `backend/pyproject.toml` with their reasons.

CI enforces the first three. It does not enforce coverage thresholds, and `just ci-backend` measures coverage that
nothing acts on — treat the number as information, not as a gate.

## Data layer

The SQLAlchemy models in `backend/app/db/models.py` are the single schema definition of the `fv` dataset. Alembic
migrates it on **BigQuery** in the cloud (`sqlalchemy-bigquery`) and on **DuckDB** locally (`duckdb-engine`), so the
same SQL runs in both. The domain tools read only through `warehouse.query()`, which rejects anything that is not a
single allowlisted `SELECT` with a `LIMIT`. `backend/tests/integration/test_migrations.py` fails if a migration
produces anything other than the tables and column types the models declare.

## Google Cloud services

Vertex AI (Gemini, ADK agents), Cloud Run, BigQuery and BigQuery ML, Firestore, Pub/Sub (with a BigQuery subscription
for plant events and a dead-letter topic), Cloud Storage, Cloud Build, Artifact Registry, Cloud Logging. Region
`us-central1`; Gemini from the `global` endpoint.

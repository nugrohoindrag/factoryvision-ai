# Testing

Factory Vision AI follows a test pyramid: many fast tests at the base, a few slow ones at the top.

| Layer | Where | What it covers | Needs | Run |
|---|---|---|---|---|
| **Unit** | `backend/tests/unit/`, `frontend/src/**/*.test.ts(x)` | Pure logic: SQL guardrails, error catalog, plant spec, seed rules and determinism, formatting, components | Nothing | `pytest -m unit` · `npm test` |
| **Integration** | `backend/tests/integration/` | A real DuckDB warehouse built from a fresh seed **through the Alembic migrations**; the seven planted data stories at tool level; KPI formulas against reference SQL; CP-SAT feasibility and time limit; API error shapes (FastAPI TestClient) | Python deps only, no network | `pytest -m integration` |
| **E2E** | `backend/tests/e2e/`, `frontend/e2e/` | A running fv-app (local or Cloud Run): API smoke; browser flows on desktop and phone (Playwright) | Running app; Vertex AI for live agent runs | `FV_E2E_BASE_URL=… pytest -m e2e` · `npm run test:e2e` |
| **Agent evals** | `evals/` | Agents (not tools) find the planted stories within tolerance | Vertex AI | `adk eval` (Phase 3) |

Markers are assigned from the directory (`backend/tests/conftest.py`), so a test's layer is where it lives.

## Ground truth

`evals/ground_truth.json` holds the seven planted stories and their tolerances (PRD §9: percentages ±5 points,
day ranges ±1 day, entities exact). The same file drives `test_stories.py` (tools, every CI run) and the agent
evals (against the deployment). If you change a story knob in `backend/simulator/seed/generator.py`, the
integration tests tell you whether the stories still land.

## Rules

- No test calls Gemini or Google Cloud unless it is under `e2e` or `evals`.
- Integration tests never touch `data/local/`: they build their own seed and warehouse in a temp directory.
- A migration must produce exactly the tables and column types of the SQLAlchemy models (`test_migrations.py`).
- `docs/error-catalog.md` is generated; `test_errors.py` fails if it is out of date (`python -m app.errors`).

## CI

`.github/workflows/ci.yml` runs backend unit and integration tests, and frontend typecheck,
unit tests and a Playwright smoke test on every push and pull request. It does not collect
coverage; `just ci-backend` does, and no threshold is enforced on either surface.

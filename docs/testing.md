# Testing

Factory Vision AI follows a test pyramid: many fast tests at the base, a few slow ones at the top.

| Layer | Where | What it covers | Needs | Run |
|---|---|---|---|---|
| **Unit** | `backend/tests/unit/`, `frontend/src/**/*.test.ts(x)` | Pure logic: SQL guardrails, error catalog, plant spec, seed rules and determinism, the Action Center state machine, the twin simulator (determinism, andon rules, commands), the Gemini wrappers with Gemini mocked (vision cache, debounce and cap, QC verdicts), formatting, components | Nothing | `just test-unit` · `npm test` |
| **Integration** | `backend/tests/integration/` | A real DuckDB warehouse built from a fresh seed **through the Alembic migrations**; the seven planted data stories at tool level; KPI formulas against reference SQL; CP-SAT feasibility and time limit; `impact_calculator`; the API (briefing, module pages, Action Center, twin, Pub/Sub push, Replay) through FastAPI TestClient; the ADK agents driven by a scripted fake model | Python deps only, no network | `just test-integration` |
| **E2E** | `backend/tests/e2e/`, `frontend/e2e/` | A running fv-app (local or Cloud Run): API smoke and the Replay judge path; browser flows on desktop and phone (Playwright) | Running app | `FV_E2E_BASE_URL=… pytest -m e2e` · `npm run test:e2e` |
| **Agent eval** | `backend/tests/e2e/test_agents_live.py` + `evals/ground_truth.json` | One live Disruption Response must turn the planted stories into proposals with exact entities | Vertex AI (billed) | `FV_E2E_BASE_URL=… FV_E2E_LIVE=1 pytest -m e2e` |

Markers are assigned from the directory (`backend/tests/conftest.py`), so a test's layer is where it lives.

## Ground truth

`evals/ground_truth.json` holds the seven planted stories and their tolerances (PRD §9: percentages ±5 points,
day ranges ±1 day, entities exact). The same file drives `test_stories.py` (tools, every CI run), `test_replay.py`
(the shipped recording) and the live agent eval (against a deployment). If you change a story knob in
`backend/simulator/seed/generator.py`, the integration tests tell you whether the stories still land.

## Rules

- No test calls Gemini or Google Cloud unless it is under `e2e`. Gemini is replaced by monkeypatched functions in unit
  tests and by `ScriptedLlm`, a `BaseLlm` that returns planned function calls, in `test_agents_plumbing.py`.
- Integration tests never touch `data/local/`: they build their own seed and warehouse in a temp directory.
- A migration must produce exactly the tables and column types of the SQLAlchemy models (`test_migrations.py`).
- `docs/error-catalog.md` is generated; `test_errors.py` fails if it is out of date (`just catalog`).
- `test_fresh_scripted_recording_matches_shipped_one` re-records the scripted Replay and compares the proposals, so a
  tool change that moves a number fails until `data/replays/disruption_response.json` is re-recorded.

## CI

`.github/workflows/ci.yml` runs backend unit and integration tests, and frontend typecheck,
unit tests and a Playwright smoke test on every push and pull request. It does not collect
coverage; `just ci-backend` does, and no threshold is enforced on either surface. No CI job calls Gemini.

# Factory Vision AI

Agentic Order-to-Delivery copilot with a live digital twin for mid-sized manufacturers.
Built for the Google Cloud AI Builder Cup 2026 — Manufacturing: Intelligent Operations & Industrial Efficiency.

Eight AI agents (Google ADK + Gemini on Vertex AI) connect seven modules — Demand Forecast,
Production Planning, MES, Material Supply, Purchasing, Quality Control and Delivery — on top of
**Vidad**, a simulated car-bumper plant (injection molding, painting, assembly). Every insight becomes
a proposed action that a human approves, and the plant reacts.

> Work in progress. All company, customer, vendor and car-model names are fictional.

## Monorepo layout

| Path | Contents |
|---|---|
| `backend/` | Python 3.12: FastAPI service (`app/`), ADK agents and deterministic tools (`app/tools/`), SQLAlchemy models and Alembic migrations (`app/db/`, `migrations/`), plant simulator and seed generator (`simulator/`), tests |
| `frontend/` | React + Vite + TypeScript UI (3D twin with React Three Fiber, module pages, Action Center), Vitest and Playwright tests |
| `data/` | Generated seed CSVs and the local DuckDB warehouse (git-ignored; rebuilt by the commands below) |
| `evals/` | Ground truth for the planted data stories |
| `infra/` | Google Cloud setup scripts |
| `docs/` | [Testing](docs/testing.md), [error catalog](docs/error-catalog.md) |

## Data layer

The SQLAlchemy models in `backend/app/db/models.py` are the single schema definition of the `fv` analytics
dataset. Alembic migrates it on **BigQuery** in the cloud (`sqlalchemy-bigquery`) and on **DuckDB** locally
(`duckdb_engine`), so the same SQL runs in both. Agent tools only read through a guarded query function:
one SELECT, allowlisted tables, always a LIMIT. Operational state (proposals, sessions, vision cache) lives in
Firestore.

## Running locally

```bash
# Backend (from backend/)
python -m venv ../.venv && ../.venv/Scripts/pip install -r requirements.txt   # Windows; use bin/ on Linux/macOS
python -m simulator.seed.generator        # 90-day seed -> ../data/seed
python -m app.db.build_local              # Alembic upgrade + load -> ../data/local/fv.duckdb
pytest                                    # unit + integration
uvicorn app.main:app --reload --port 8080

# Frontend (from frontend/)
npm install
npm run dev                               # http://localhost:5173, proxies /api to :8080
npm test
```

## Google Cloud services

Vertex AI (Gemini), Cloud Run, BigQuery and BigQuery ML, Firestore, Pub/Sub, Cloud Storage,
Firebase Auth, Cloud Build, Artifact Registry, Secret Manager, Cloud Logging and Cloud Trace.

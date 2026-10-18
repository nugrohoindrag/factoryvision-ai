# fv-app: one Cloud Run service = FastAPI backend + built React frontend.
# Build: gcloud builds submit --tag us-central1-docker.pkg.dev/$PROJECT/fv/fv-app (see infra/deploy.sh)

FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx vite build

FROM python:3.12-slim
# Same uv as development, so the image installs exactly what backend/uv.lock pins (no dev groups).
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH \
    FV_WEB_DIST=/app/frontend/dist FV_SEED_DIR=/app/data/seed FV_LOCAL_DB=/app/data/local/fv.duckdb \
    FV_REPLAY_DIR=/app/data/replays
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY evals/ /app/evals/
COPY data/replays/ /app/data/replays/
# Reviewed Vidad photos (fictional names only), built locally by `python -m simulator.photos`.
COPY data/photos/ /app/data/photos/
# The local DuckDB warehouse is a fallback (FV_WAREHOUSE=local) if BigQuery is not reachable.
RUN python -m simulator.seed.generator --out /app/data/seed > /dev/null && python -m app.db.build_local
COPY --from=web /web/dist /app/frontend/dist
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers

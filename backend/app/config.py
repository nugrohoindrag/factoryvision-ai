"""Runtime settings, read from the environment. Model IDs live only here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    project: str = _env("GOOGLE_CLOUD_PROJECT", "")
    region: str = _env("FV_REGION", "us-central1")
    # Gemini endpoint location can differ from the infra region (some models are served from "global").
    model_location: str = _env("FV_MODEL_LOCATION", _env("GOOGLE_CLOUD_LOCATION", "us-central1"))
    dataset: str = _env("FV_DATASET", "fv")
    warehouse: str = _env("FV_WAREHOUSE", "local")  # local (DuckDB over data/seed) | bigquery
    store: str = _env("FV_STORE", "memory")  # memory | firestore
    pubsub: str = _env("FV_PUBSUB", "local")  # local (in-process) | gcp
    seed_dir: Path = Path(_env("FV_SEED_DIR", str(ROOT / "data" / "seed")))
    web_dist: Path = Path(_env("FV_WEB_DIST", str(ROOT / "web" / "dist")))
    replay_dir: Path = Path(_env("FV_REPLAY_DIR", str(ROOT / "data" / "replays")))
    docs_dir: Path = Path(_env("FV_DOCS_DIR", str(ROOT / "data" / "docs")))
    bucket_prefix: str = _env("FV_BUCKET_PREFIX", "")
    # Models (PRD §8). Never gemini-2.5-*.
    model_orchestrator: str = _env("FV_MODEL_ORCHESTRATOR", "gemini-4-argon")
    model_module: str = _env("FV_MODEL_MODULE", "gemini-3.8-flash")
    model_vision: str = _env("FV_MODEL_VISION", "gemini-3.5-flash-lite")
    model_live: str = _env("FV_MODEL_LIVE", "gemini-3.8-flash-live")
    # Cost guards for live sessions.
    vision_calls_per_session: int = int(_env("FV_VISION_CAP", "20"))
    vision_debounce_s: int = int(_env("FV_VISION_DEBOUNCE_S", "30"))
    live_runs_per_session: int = int(_env("FV_LIVE_RUN_CAP", "5"))
    agent_asks_per_session: int = int(_env("FV_AGENT_ASK_CAP", "30"))
    qc_live_seconds: int = int(_env("FV_QC_LIVE_SECONDS", "60"))
    require_auth: bool = _env("FV_REQUIRE_AUTH", "false").lower() == "true"


settings = Settings()

for _m in (settings.model_orchestrator, settings.model_module, settings.model_vision, settings.model_live):
    if _m.startswith("gemini-2.5"):
        raise RuntimeError(f"Model {_m} is not allowed (gemini-2.5-* is shut down on Vertex AI 20 Oct 2026)")

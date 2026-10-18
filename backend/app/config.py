from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

SHUTDOWN_MODEL_PREFIX = "gemini-2.5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FV_", extra="ignore")

    project: str = Field(default="", validation_alias="GOOGLE_CLOUD_PROJECT")
    region: str = "us-central1"
    model_location: str = Field(default="global", validation_alias=AliasChoices("FV_MODEL_LOCATION", "GOOGLE_CLOUD_LOCATION"))
    dataset: str = "fv"
    warehouse: Literal["local", "bigquery"] = "local"
    store: Literal["memory", "firestore"] = "memory"
    pubsub: Literal["local", "gcp"] = "local"
    ai: Literal["auto", "vertex", "off"] = "auto"
    seed_dir: Path = ROOT / "data" / "seed"
    replay_dir: Path = ROOT / "data" / "replays"
    docs_dir: Path = ROOT / "data" / "docs"
    web_dist: Path = ROOT / "frontend" / "dist"
    photos_dir: Path = ROOT / "data" / "photos"
    photos_require_review: bool = False
    bucket_prefix: str = ""
    push_audience: str = ""
    push_service_account: str = ""
    model_orchestrator: str = "gemini-4-argon"
    model_module: str = "gemini-3.7-flash"
    model_vision: str = "gemini-3.5-flash-lite"
    model_live: str = "gemini-3.8-flash-live"
    vision_calls_per_session: int = Field(default=20, ge=1, le=1000, validation_alias="FV_VISION_CAP")
    vision_debounce_s: int = Field(default=30, ge=0, validation_alias="FV_VISION_DEBOUNCE_S")
    live_runs_per_session: int = Field(default=5, ge=1, validation_alias="FV_LIVE_RUN_CAP")
    agent_asks_per_session: int = Field(default=30, ge=1, validation_alias="FV_AGENT_ASK_CAP")
    ai_daily_cap: int = Field(default=400, ge=1, validation_alias="FV_AI_DAILY_CAP")
    qc_live_seconds: int = Field(default=60, ge=1)
    require_auth: bool = False
    # Outbox: approved actions become emails. Sent through Gmail only when the OAuth JSON (Secret Manager
    # fv-gmail-oauth, mounted as FV_GMAIL_OAUTH) and a base address are set, and only to plus-addresses of that base.
    mail_base: str = ""
    gmail_oauth: str = ""
    mail_sends_per_session: int = Field(default=60, ge=0, validation_alias="FV_MAIL_CAP")
    # Sign in with Google (Firebase Auth web config; public values, served to the browser at runtime) and the
    # comma-separated admin accounts that approve registrations.
    firebase_api_key: str = ""
    firebase_app_id: str = ""
    admin_emails: str = ""

    @field_validator("model_orchestrator", "model_module", "model_vision", "model_live")
    @classmethod
    def _reject_shutdown_model(cls, value: str) -> str:
        if value.startswith(SHUTDOWN_MODEL_PREFIX):
            msg = f"Model {value} is not allowed ({SHUTDOWN_MODEL_PREFIX}-* is shut down on Vertex AI 20 Oct 2026)"
            raise ValueError(msg)
        return value


settings = Settings()

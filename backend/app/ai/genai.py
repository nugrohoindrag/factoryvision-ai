from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.errors import FVError

if TYPE_CHECKING:
    from google import genai
    from google.genai import types

MODEL_UNAVAILABLE = "FV-AI-001"
INVALID_MODEL_OUTPUT = "FV-AI-002"

log = logging.getLogger("fv.ai")


def enabled() -> bool:
    return settings.ai == "vertex" or (settings.ai == "auto" and bool(settings.project))


@lru_cache(maxsize=1)
def client() -> genai.Client:
    from google import genai

    return genai.Client(vertexai=True, project=settings.project, location=settings.model_location)


def part_bytes(data: bytes, mime_type: str) -> types.Part:
    from google.genai import types

    return types.Part.from_bytes(data=data, mime_type=mime_type)


def generate_json(model: str, contents: list[Any], schema: dict[str, Any], system: str | None = None, temperature: float = 0.1) -> dict[str, Any]:
    if not enabled():
        raise FVError(MODEL_UNAVAILABLE, "AI is disabled in this environment (FV_AI=off); use Replay", {"model": model})
    from google.genai import types

    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, system_instruction=system, temperature=temperature)
    try:
        resp = client().models.generate_content(model=model, contents=contents, config=cfg)
    except Exception as e:
        log.warning("gemini call failed: %s", e)
        raise FVError(MODEL_UNAVAILABLE, detail={"model": model, "error": str(e)[:300]}) from e
    try:
        parsed = json.loads(resp.text or "")
    except (TypeError, ValueError) as e:
        log.warning("invalid model output: %r", (resp.text or "")[:500])
        raise FVError(INVALID_MODEL_OUTPUT, detail={"model": model}) from e
    if not isinstance(parsed, dict):
        raise FVError(INVALID_MODEL_OUTPUT, detail={"model": model, "type": type(parsed).__name__})
    return parsed

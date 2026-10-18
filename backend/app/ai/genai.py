from __future__ import annotations

import json
import logging
from functools import lru_cache

from app.config import settings
from app.errors import FVError

log = logging.getLogger("fv.ai")


def enabled() -> bool:
    return settings.ai == "vertex" or (settings.ai == "auto" and bool(settings.project))


@lru_cache(maxsize=1)
def client():
    from google import genai

    return genai.Client(vertexai=True, project=settings.project, location=settings.model_location)


def part_bytes(data: bytes, mime_type: str):
    from google.genai import types

    return types.Part.from_bytes(data=data, mime_type=mime_type)


def generate_json(model: str, contents: list, schema: dict, system: str | None = None, temperature: float = 0.1) -> dict:
    if not enabled():
        raise FVError("FV-AI-001", "AI is disabled in this environment (FV_AI=off); use Replay", {"model": model})
    from google.genai import types

    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema,
                                      system_instruction=system, temperature=temperature)
    try:
        resp = client().models.generate_content(model=model, contents=contents, config=cfg)
    except Exception as e:
        log.warning("gemini call failed: %s", e)
        raise FVError("FV-AI-001", detail={"model": model, "error": str(e)[:300]}) from e
    try:
        return json.loads(resp.text)
    except (TypeError, ValueError) as e:
        log.warning("invalid model output: %r", (resp.text or "")[:500])
        raise FVError("FV-AI-002", detail={"model": model}) from e

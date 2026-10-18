from __future__ import annotations

from typing import Any

from app.config import settings
from simulator.photos import load


def catalog() -> list[dict[str, Any]]:
    return load(settings.photos_dir, require_review=settings.photos_require_review)


def find(*, photo_id: str | None = None, file: str | None = None) -> dict[str, Any] | None:
    return next((p for p in catalog() if (photo_id and p["photo_id"] == photo_id) or (file and p["file"] == file)), None)


def mime_type(entry: dict[str, Any]) -> str:
    return "image/png" if entry["file"].endswith(".png") else "image/jpeg"


def read(entry: dict[str, Any]) -> bytes:
    return (settings.photos_dir / entry["file"]).read_bytes()

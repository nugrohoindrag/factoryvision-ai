from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from pathlib import Path


def load(folder: Path, *, require_review: bool = False) -> list[dict[str, Any]]:
    p = folder / "catalog.json"
    cat: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if require_review:
        cat = [c for c in cat if c.get("reviewed")]
    return cat


def catalog() -> list[dict[str, Any]]:
    return load(settings.photos_dir, require_review=settings.photos_require_review)


def find(*, photo_id: str | None = None, file: str | None = None) -> dict[str, Any] | None:
    return next((p for p in catalog() if (photo_id and p["photo_id"] == photo_id) or (file and p["file"] == file)), None)


def mime_type(entry: dict[str, Any]) -> str:
    return "image/png" if entry["file"].endswith(".png") else "image/jpeg"


def read(entry: dict[str, Any]) -> bytes:
    return (settings.photos_dir / entry["file"]).read_bytes()

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from app import plant
from app.photos import load

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "vidad-raw"
OUT = ROOT / "data" / "photos"
_LABEL = re.compile(r"^(FR|RR)[- ]?([A-Z0-9]{3})$")
_FILE = re.compile(r"^(\d+)_(.+)_([^_]+)$")


def _photo_dir() -> Path:
    return next(RAW.glob("*/BUMPER-MODEL-COLOR/images"), RAW / "BUMPER-MODEL-COLOR" / "images")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build(raw_dir: Path | None = None, out: Path = OUT, amap_path: Path = RAW / "anonymization-map.json") -> list[dict[str, Any]]:
    raw_dir = raw_dir or _photo_dir()
    amap = json.loads(amap_path.read_text(encoding="utf-8"))
    models = {_norm(k): v for k, v in amap["models"].items()}
    colors = {k: v for k, v in amap["colors"].items() if not k.startswith("_")}
    out.mkdir(parents=True, exist_ok=True)
    kept = {c["photo_id"]: c for c in load(out)}
    catalog = []
    for f in sorted(raw_dir.iterdir()):
        m = _FILE.match(f.stem)
        if not m:
            continue
        num, real_model, label = m.groups()
        model = models.get(_norm(real_model))
        lm = _LABEL.match(label.upper().replace("RRX", "RR-X"))
        if not model or not lm or "sku_front" not in model:
            continue
        sku = model["sku_front"] if lm.group(1) == "FR" else model["sku_rear"]
        color = colors.get(lm.group(2))
        if color is None or sku not in plant.PRODUCT_BY_SKU:
            continue
        photo_id = f"P{int(num):03d}"
        name = f"{photo_id}_{sku}_{color}{f.suffix.lower()}"
        shutil.copyfile(f, out / name)
        previous = kept.get(photo_id, {})
        catalog.append(
            {
                "photo_id": photo_id,
                "file": name,
                "sku": sku,
                "model": model["model"],
                "position": "Front" if lm.group(1) == "FR" else "Rear",
                "color": color,
                "defect_code": previous.get("defect_code"),
                "reviewed": previous.get("reviewed", False),
            }
        )
    (out / "catalog.json").write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    return catalog



def main() -> None:
    cat = build()
    by_sku: dict[str, int] = {}
    for c in cat:
        by_sku[c["sku"]] = by_sku.get(c["sku"], 0) + 1
    sys.stdout.write(f"{len(cat)} photos -> {OUT} {by_sku}\n")


if __name__ == "__main__":
    main()

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


# synthetic-defects/labels.json class -> plant.DEFECT_CLASSES code
DEFECT_CODES = {
    "Scratch": "SCRATCH",
    "Bubble/waviness": "BUBBLE",
    "Dust seed": "DUST_SEED",
    "Sag/run": "SAG_RUN",
    "Orange peel": "ORANGE_PEEL",
    "Blister": "BLISTER",
}


def build_defects(out: Path = OUT, raw: Path = RAW / "synthetic-defects", amap_path: Path = RAW / "anonymization-map.json") -> list[dict[str, Any]]:
    """Add the defect images to the catalog. They are procedurally rendered defects on real Vidad photos, so each entry
    says `synthetic: true` and the UI labels it. Only images whose source photo is a reviewed demo photo are kept, and
    they are never used as clean QC references."""
    amap = json.loads(amap_path.read_text(encoding="utf-8"))
    models = {_norm(k): v for k, v in amap["models"].items()}
    colors = {k: v for k, v in amap["colors"].items() if not k.startswith("_")}
    catalog = [c for c in load(out) if not c.get("synthetic")]
    reviewed = {c["photo_id"]: c for c in catalog if c.get("reviewed")}
    added = []
    for lab in json.loads((raw / "labels.json").read_text(encoding="utf-8")):
        code = DEFECT_CODES.get(str(lab.get("class")))
        m = _FILE.match(Path(str(lab.get("source", ""))).stem)
        if not code or not m:
            continue
        num, real_model, label = m.groups()
        base = reviewed.get(f"P{int(num):03d}")
        model = models.get(_norm(real_model))
        lm = _LABEL.match(label.upper())
        if base is None or not model or not lm or "sku_front" not in model or colors.get(lm.group(2)) != base["color"]:
            continue
        photo_id = f"D{str(lab['id'])[:3]}"
        name = f"{photo_id}_{base['sku']}_{base['color']}_{code}.png"
        shutil.copyfile(raw / str(lab["full"]), out / name)
        added.append(
            {
                "photo_id": photo_id,
                "file": name,
                "sku": base["sku"],
                "model": base["model"],
                "position": base["position"],
                "color": base["color"],
                "defect_code": code,
                "synthetic": True,
                "source_photo": base["photo_id"],
                "reviewed": True,
            }
        )
    (out / "catalog.json").write_text(json.dumps(catalog + added, indent=1), encoding="utf-8")
    return added


def main() -> None:
    if sys.argv[1:] == ["defects"]:
        added = build_defects()
        sys.stdout.write(f"{len(added)} synthetic defect photos -> {OUT}\n")
        return
    cat = build()
    by_sku: dict[str, int] = {}
    for c in cat:
        by_sku[c["sku"]] = by_sku.get(c["sku"], 0) + 1
    sys.stdout.write(f"{len(cat)} photos -> {OUT} {by_sku}\n")


if __name__ == "__main__":
    main()

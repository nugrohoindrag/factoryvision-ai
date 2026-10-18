from __future__ import annotations

from typing import Any

from app import photos, plant
from app.ai import genai
from app.config import settings
from app.errors import FVError

UNKNOWN_ENTITY = "FV-DATA-001"
INVALID_PHOTO = "FV-QC-001"

PO_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "customer_name": {"type": "STRING"},
        "po_number": {"type": "STRING"},
        "issue_date": {"type": "STRING", "description": "YYYY-MM-DD"},
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sku": {"type": "STRING"},
                    "month": {"type": "STRING", "description": "YYYY-MM"},
                    "qty": {"type": "INTEGER"},
                    "previous_qty": {"type": "INTEGER", "nullable": True},
                },
                "required": ["sku", "qty"],
            },
        },
        "notes": {"type": "STRING"},
    },
    "required": ["customer_name", "po_number", "lines"],
}
EMAIL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "vendor_name": {"type": "STRING"},
        "po_id": {"type": "STRING"},
        "material_id": {"type": "STRING"},
        "original_date": {"type": "STRING", "description": "YYYY-MM-DD"},
        "new_date": {"type": "STRING", "description": "YYYY-MM-DD"},
        "reason": {"type": "STRING"},
    },
    "required": ["vendor_name", "new_date"],
}
DEFECT_CODES = [d[0] for d in plant.DEFECT_CLASSES]
PHOTO_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "model": {"type": "STRING", "enum": ["ARKA", "BROMO", "CITRA", "UNKNOWN"]},
        "position": {"type": "STRING", "enum": ["Front", "Rear", "UNKNOWN"]},
        "same_model_as_reference": {"type": "BOOLEAN"},
        "same_color_as_reference": {"type": "BOOLEAN"},
        "color_description": {"type": "STRING"},
        "defects": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"defect_code": {"type": "STRING", "enum": DEFECT_CODES}, "location": {"type": "STRING"}, "confidence": {"type": "NUMBER"}},
                "required": ["defect_code", "confidence"],
            },
        },
        "confidence": {"type": "NUMBER"},
    },
    "required": ["model", "position", "same_model_as_reference", "same_color_as_reference", "defects", "confidence"],
}
MAX_PHOTO_BYTES = 8_000_000


def read_customer_po(pdf: bytes) -> dict[str, Any]:
    out = genai.generate_json(
        settings.model_module,
        [
            genai.part_bytes(pdf, "application/pdf"),
            "Extract the purchase order. SKUs look like FB-ARKA. If the document shows a previous quantity, fill previous_qty.",
        ],
        PO_SCHEMA,
        "You extract data from customer purchase orders exactly as written. Never compute or guess numbers.",
    )
    for line in out.get("lines", []):
        if line.get("previous_qty"):
            line["change_pct"] = round(100 * (line["qty"] / line["previous_qty"] - 1), 1)
    return out


def read_vendor_email(text: str) -> dict[str, Any]:
    out = genai.generate_json(
        settings.model_module, [text], EMAIL_SCHEMA, "You extract delivery changes from vendor emails exactly as written. Dates as YYYY-MM-DD."
    )
    if out.get("original_date") and out.get("new_date"):
        from datetime import date

        out["delay_days"] = (date.fromisoformat(out["new_date"]) - date.fromisoformat(out["original_date"])).days
    return out


def references(sku: str, color: str, exclude: str | None = None) -> list[dict[str, Any]]:
    cat = [p for p in photos.catalog() if p["photo_id"] != exclude]
    same = [p for p in cat if p["sku"] == sku and p["color"] == color][:1]
    other = [p for p in cat if p["sku"] == sku and p["color"] != color][:1]
    return same + other


def inspect_photo(image: bytes, mime_type: str, expected_sku: str, expected_color: str, photo_id: str | None = None) -> dict[str, Any]:
    if mime_type not in ("image/jpeg", "image/png") or not image or len(image) > MAX_PHOTO_BYTES:
        raise FVError(INVALID_PHOTO, detail={"mime_type": mime_type, "bytes": len(image or b"")})
    if expected_sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {expected_sku}")
    refs = references(expected_sku, expected_color, photo_id)
    contents: list[Any] = []
    for k, r in enumerate(refs):
        contents += [
            f"Reference {k + 1}: {r['sku']} ({r['model']} {r['position']}) in colour {r['color']}.",
            genai.part_bytes(photos.read(r), photos.mime_type(r)),
        ]
    contents += [
        f"Inspected bumper. The order line expects {expected_sku} in colour {expected_color}" + (" (reference 1 shows that SKU and colour)." if refs else "."),
        genai.part_bytes(image, mime_type),
    ]
    ng = "; ".join(f"{d[0]} = {d[2]} ({d[3]})" for d in plant.DEFECT_CLASSES)
    out = genai.generate_json(
        settings.model_module,
        contents,
        PHOTO_SCHEMA,
        "You are a QC inspector for painted car bumpers. Compare the inspected bumper with the references: "
        "same model and position? same paint colour? Then list visible paint or molding defects using only these codes: "
        f"{ng}. Report no defect unless it is clearly visible.",
    )
    color_ok = out["same_color_as_reference"]
    model_ok = out["same_model_as_reference"]
    out["defects"] = [
        {
            **d,
            "defect_name": plant.DEFECT_BY_CODE[d["defect_code"]][2],
            "ng_group": plant.DEFECT_BY_CODE[d["defect_code"]][3],
            "category": plant.DEFECT_BY_CODE[d["defect_code"]][4],
            "disposition": plant.DEFECT_BY_CODE[d["defect_code"]][5],
        }
        for d in out.get("defects", [])
        if d["defect_code"] in plant.DEFECT_BY_CODE
    ]
    out.update(
        {
            "expected_sku": expected_sku,
            "expected_color": expected_color,
            "verdict": verdict(model_ok=bool(model_ok), color_ok=bool(color_ok), defects=out["defects"]),
            "references": [r["photo_id"] for r in refs],
        }
    )
    return out


def verdict(*, model_ok: bool, color_ok: bool, defects: list[dict[str, Any]]) -> str:
    if not model_ok:
        return "WRONG_PART"
    if not color_ok:
        return "WRONG_COLOR"
    if any(d["disposition"] == "Scrap" for d in defects):
        return "SCRAP"
    if defects:
        return str(defects[0]["disposition"]).upper().replace(" ", "_")
    return "OK"

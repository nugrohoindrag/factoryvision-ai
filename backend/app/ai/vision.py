from __future__ import annotations

from datetime import datetime, timezone

from app.ai import genai
from app.config import settings
from app.errors import FVError
from app.session import limiter
from app.store import store

CAMERAS = {
    "CAM-Molding": "the injection molding hall: six machines IMM-01..IMM-06, each with an ID board and a large andon light on top "
                   "(green = running, yellow = slow or mold change, red = stopped, blue = maintenance, grey = idle). "
                   "A small speed gauge under each board shows performance in percent.",
    "CAM-WIP": "the WIP racks between molding and painting: one rack per SKU (FB-ARKA, RB-ARKA, FB-BROMO, RB-BROMO, FB-CITRA, RB-CITRA), "
               "each labelled, holding unpainted black bumpers. Count the bumpers on each rack (a rack holds at most 40).",
    "CAM-Warehouse": "the finished-goods warehouse: returnable racks per SKU with painted bumpers. Racks on quality hold are orange.",
}
OBS_TYPES = ["machine_slow", "machine_down", "machine_setup", "machine_maintenance", "machine_idle", "wip_low", "wip_high",
             "rack_on_hold", "warehouse_fill", "other"]
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "camera_id": {"type": "STRING"},
        "observations": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "type": {"type": "STRING", "enum": OBS_TYPES},
            "subject": {"type": "STRING", "description": "machine ID (IMM-04) or SKU (FB-ARKA)"},
            "value": {"type": "NUMBER", "nullable": True, "description": "count, percent or null"},
            "severity": {"type": "STRING", "enum": ["low", "medium", "high"]},
            "confidence": {"type": "NUMBER"},
        }, "required": ["type", "subject", "severity", "confidence"]}},
    },
    "required": ["camera_id", "observations"],
}
SYSTEM = ("You are the Twin Vision Analyzer of a car-bumper plant. You get one frame from a fixed camera. "
          "Report only what is visible and abnormal: slow, stopped, changing-over or maintained machines; "
          "WIP racks with fewer than 10 bumpers (wip_low, high severity below 5) or full racks; racks on hold. "
          "Use the IDs written on the boards. Do not invent machines or SKUs. Return an empty list when everything looks normal.")
MAX_FRAME_BYTES = 2_000_000


def analyze(camera_id: str, image: bytes, mime_type: str = "image/jpeg") -> dict:
    if camera_id not in CAMERAS:
        raise FVError("FV-VIS-003", f"unknown camera {camera_id}", {"valid": list(CAMERAS)})
    prompt = f"Camera {camera_id} shows {CAMERAS[camera_id]} Return camera_id={camera_id}."
    out = genai.generate_json(settings.model_vision, [genai.part_bytes(image, mime_type), prompt], SCHEMA, SYSTEM)
    out["camera_id"] = camera_id
    return out


def check_frame(image: bytes, mime_type: str) -> None:
    if mime_type not in ("image/jpeg", "image/png") or not image or len(image) > MAX_FRAME_BYTES:
        raise FVError("FV-VIS-003", detail={"mime_type": mime_type, "bytes": len(image or b"")})


def frame(session_id: str, camera_id: str, state_hash: str, image: bytes, mime_type: str, snapshot: dict | None) -> dict:
    check_frame(image, mime_type)
    key = f"{camera_id}:{state_hash}"
    cached = store().get("vision_cache", key)
    if cached:
        result = {**cached["result"], "cached": True}
    else:
        limiter.debounce(session_id, f"cam:{camera_id}", settings.vision_debounce_s, "FV-VIS-002")
        remaining = limiter.take(session_id, "vision", settings.vision_calls_per_session, "FV-VIS-001")
        result = analyze(camera_id, image, mime_type)
        store().set("vision_cache", key, {"result": result, "created_at": datetime.now(timezone.utc).isoformat()})
        result = {**result, "cached": False, "calls_left": remaining}
    if snapshot:
        result["score"] = score(result["observations"], snapshot, camera_id)
    record(session_id, result)
    return result


def record(session_id: str, result: dict) -> None:
    doc = store().get("observations", session_id) or {"session_id": session_id, "cameras": {}}
    doc["cameras"][result["camera_id"]] = {"observations": result["observations"], "at": datetime.now(timezone.utc).isoformat()}
    store().set("observations", session_id, doc)


def latest(session_id: str) -> dict:
    doc = store().get("observations", session_id)
    return doc["cameras"] if doc else {}


def truth(snapshot: dict) -> set[tuple[str, str]]:
    out = set()
    for m in snapshot["machines"]:
        if not m["machine_id"].startswith("IMM"):
            continue
        t = {"red": "machine_down", "blue": "machine_maintenance", "grey": "machine_idle"}.get(m["andon"])
        if m["andon"] == "yellow":
            t = "machine_setup" if m["status"] == "Setup" else "machine_slow"
        if t:
            out.add((t, m["machine_id"]))
    for sku, n in snapshot["wip"].items():
        if n < 10:
            out.add(("wip_low", sku))
    for sku, racks in snapshot["warehouse"].items():
        if any(r["on_hold"] for r in racks):
            out.add(("rack_on_hold", sku))
    return out


CAMERA_TYPES = {"CAM-Molding": "machine_", "CAM-WIP": "wip_", "CAM-Warehouse": "rack_"}


def score(observations: list[dict], snapshot: dict, camera_id: str) -> dict:
    seen = {(o["type"], o["subject"]) for o in observations if o["type"] != "other"}
    real = {t for t in truth(snapshot) if t[0].startswith(CAMERA_TYPES[camera_id])}
    correct = seen & real
    return {"reported": len(seen), "correct": len(correct), "precision": round(len(correct) / len(seen), 2) if seen else None,
            "missed": sorted(f"{t}:{s}" for t, s in real - seen)}

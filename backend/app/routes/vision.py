from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response

from app import photos, twin
from app.ai import extract, vision
from app.config import settings
from app.errors import FVError
from app.session import limiter, session_id
from app.store import store
from simulator.docs import DOCUMENTS

router = APIRouter(prefix="/api")


@router.post("/vision/frame")
async def vision_frame(camera_id: str = Form(...), state_hash: str = Form(...), frame: UploadFile = File(...), sid: str = Depends(session_id)) -> dict:
    image = await frame.read()
    state = twin.get(sid, create=False)
    return vision.frame(sid, camera_id, state_hash, image, frame.content_type or "", state.snapshot() if state else None)


@router.get("/vision/observations")
def vision_observations(sid: str = Depends(session_id)) -> dict:
    return {"cameras": vision.latest(sid), "calls_used": limiter.used(sid, "vision"), "cap": settings.vision_calls_per_session}


@router.post("/qc/inspect")
async def qc_inspect(photo: UploadFile | None = File(default=None), photo_id: str | None = Form(default=None),
                     expected_sku: str | None = Form(default=None), expected_color: str | None = Form(default=None),
                     sid: str = Depends(session_id)) -> dict:
    entry = photos.find(photo_id=photo_id) if photo_id else None
    if photo is not None:
        image, mime = await photo.read(), photo.content_type or ""
    elif entry:
        image, mime = photos.read(entry), photos.mime_type(entry)
    else:
        raise FVError("FV-QC-001", detail="send a photo file or a known photo_id")
    sku = expected_sku or (entry or {}).get("sku")
    color = expected_color or (entry or {}).get("color")
    if not sku or not color:
        raise FVError("FV-REQ-001", detail="expected_sku and expected_color are required for an uploaded photo")
    limiter.take(sid, "agent", settings.agent_asks_per_session, "FV-RATE-002")
    result = extract.inspect_photo(image, mime, sku, color, photo_id)
    insp_id = f"INS-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    store().set("inspections", insp_id, {"inspection_id": insp_id, "session_id": sid, "photo_id": photo_id, "result": result,
                                         "created_at": datetime.now(timezone.utc).isoformat()})
    return {"inspection_id": insp_id, **result}


@router.get("/qc/inspections")
def qc_inspections(sid: str = Depends(session_id)) -> dict:
    return {"inspections": sorted(store().where("inspections", "session_id", sid), key=lambda d: d["created_at"], reverse=True)[:50]}


@router.get("/photos")
def photos() -> dict:
    return {"photos": [{k: p[k] for k in ("photo_id", "file", "sku", "model", "position", "color", "defect_code")} for p in photos.catalog()]}


@router.get("/photos/{file}")
def photo_file(file: str):
    entry = photos.find(file=file)
    if entry is None:
        raise FVError("FV-DATA-001", f"unknown photo {file}")
    return FileResponse(settings.photos_dir / entry["file"], headers={"Cache-Control": "public, max-age=86400"})


@router.get("/documents")
def documents() -> dict:
    return {"documents": [{"name": n, "mime_type": m} for n, (m, _) in DOCUMENTS.items()]}


@router.get("/documents/{name}")
def document(name: str):
    if name not in DOCUMENTS:
        raise FVError("FV-DATA-001", f"unknown document {name}")
    mime, make = DOCUMENTS[name]
    return Response(make(), media_type=mime, headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/documents/extract")
async def document_extract(kind: str = Form(...), file: UploadFile | None = File(default=None), name: str | None = Form(default=None),
                           sid: str = Depends(session_id)) -> dict:
    if file is not None:
        data = await file.read()
    elif name in DOCUMENTS:
        data = DOCUMENTS[name][1]()
    else:
        raise FVError("FV-REQ-001", detail="send a file or a demo document name")
    limiter.take(sid, "agent", settings.agent_asks_per_session, "FV-RATE-002")
    if kind == "po":
        return {"kind": kind, **extract.read_customer_po(data)}
    if kind == "vendor_email":
        return {"kind": kind, **extract.read_vendor_email(data.decode("utf-8", "replace"))}
    raise FVError("FV-REQ-001", detail={"kind": kind, "valid": ["po", "vendor_email"]})

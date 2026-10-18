from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from app import photos, twin
from app.ai import extract, genai, vision
from app.config import settings
from app.documents import DOCUMENTS, demo_po_facts
from app.errors import FVError
from app.session import SessionId, limiter
from app.store import store
from app.tools import orders

MODEL_UNAVAILABLE = "FV-AI-001"
UNKNOWN_ENTITY = "FV-DATA-001"
DEMO_PO = "customer-po-oem-a.pdf"
MAX_PO_BYTES = 5_000_000
INVALID_PHOTO = "FV-QC-001"
INVALID_REQUEST = "FV-REQ-001"
AGENT_QUESTION_LIMIT_REACHED = "FV-RATE-002"

router = APIRouter(prefix="/api")

FormText = Annotated[str, Form()]
OptionalFormText = Annotated[str | None, Form()]
OptionalUpload = Annotated[UploadFile | None, File()]


@router.post("/vision/frame")
async def vision_frame(camera_id: FormText, state_hash: FormText, frame: Annotated[UploadFile, File()], sid: SessionId) -> dict[str, Any]:
    image = await frame.read()
    state = twin.find(sid)
    return await run_in_threadpool(vision.frame, sid, camera_id, state_hash, image, frame.content_type or "", state.snapshot() if state else None)


@router.get("/vision/observations")
def vision_observations(sid: SessionId) -> dict[str, Any]:
    return {"cameras": vision.latest(sid), "calls_used": limiter.used(sid, "vision"), "cap": settings.vision_calls_per_session}


@router.post("/qc/inspect")
async def qc_inspect(
    sid: SessionId,
    photo: OptionalUpload = None,
    photo_id: OptionalFormText = None,
    expected_sku: OptionalFormText = None,
    expected_color: OptionalFormText = None,
) -> dict[str, Any]:
    entry = photos.find(photo_id=photo_id) if photo_id else None
    if photo is not None:
        image, mime = await photo.read(), photo.content_type or ""
    elif entry:
        image, mime = photos.read(entry), photos.mime_type(entry)
    else:
        raise FVError(INVALID_PHOTO, detail="send a photo file or a known photo_id")
    sku = expected_sku or (entry or {}).get("sku")
    color = expected_color or (entry or {}).get("color")
    if not sku or not color:
        raise FVError(INVALID_REQUEST, detail="expected_sku and expected_color are required for an uploaded photo")
    limiter.take(sid, "agent", settings.agent_asks_per_session, AGENT_QUESTION_LIMIT_REACHED)
    result = await run_in_threadpool(extract.inspect_photo, image, mime, sku, color, photo_id)
    insp_id = f"INS-{datetime.now(UTC).strftime('%H%M%S%f')}"
    store().set(
        "inspections",
        insp_id,
        {"inspection_id": insp_id, "session_id": sid, "photo_id": photo_id, "result": result, "created_at": datetime.now(UTC).isoformat()},
    )
    return {"inspection_id": insp_id, **result}


@router.get("/qc/inspections")
def qc_inspections(sid: SessionId) -> dict[str, Any]:
    return {"inspections": sorted(store().where("inspections", "session_id", sid), key=lambda d: str(d["created_at"]), reverse=True)[:50]}


@router.get("/photos")
def list_photos() -> dict[str, Any]:
    keys = ("photo_id", "file", "sku", "model", "position", "color", "defect_code")
    return {"photos": [{**{k: p[k] for k in keys}, "synthetic": bool(p.get("synthetic"))} for p in photos.catalog()]}


@router.get("/photos/{file}")
def photo_file(file: str) -> FileResponse:
    entry = photos.find(file=file)
    if entry is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown photo {file}")
    return FileResponse(settings.photos_dir / entry["file"], headers={"Cache-Control": "public, max-age=86400"})


@router.get("/documents")
def documents() -> dict[str, Any]:
    return {"documents": [{"name": n, "mime_type": m} for n, (m, _) in DOCUMENTS.items()]}


@router.get("/documents/{name}")
def document(name: str) -> Response:
    if name not in DOCUMENTS:
        raise FVError(UNKNOWN_ENTITY, f"unknown document {name}")
    mime, make = DOCUMENTS[name]
    return Response(make(), media_type=mime, headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/orders/po/read")
async def read_po(sid: SessionId, file: OptionalUpload = None, name: OptionalFormText = None, replay: OptionalFormText = None) -> dict[str, Any]:
    """Customer Order intake (DF-2): read a customer PO, then check it against master data and the schedule on file."""
    if file is None and name not in DOCUMENTS:
        raise FVError(INVALID_REQUEST, detail="send a PDF file or a demo document name")
    if file is None and (replay == "true" or not genai.enabled()) and name == DEMO_PO:
        po, engine = demo_po_facts(), "recorded extraction (Replay, no Gemini call)"
    else:
        if not genai.enabled():
            raise FVError(MODEL_UNAVAILABLE, "AI is disabled in this environment; use the demo PO in Replay")
        data = await file.read() if file is not None else DOCUMENTS[str(name)][1]()
        if file is not None and (file.content_type != "application/pdf" or len(data) > MAX_PO_BYTES):
            raise FVError(INVALID_REQUEST, detail={"content_type": file.content_type, "bytes": len(data), "expected": "application/pdf up to 5 MB"})
        limiter.take(sid, "agent", settings.agent_asks_per_session, AGENT_QUESTION_LIMIT_REACHED)
        po, engine = await run_in_threadpool(extract.read_customer_po, data), f"Document & Photo Extractor ({settings.model_module})"
    return {"extracted": po, "engine": engine, "check": orders.check_po(po)}


@router.post("/documents/extract")
async def document_extract(kind: FormText, sid: SessionId, file: OptionalUpload = None, name: OptionalFormText = None) -> dict[str, Any]:
    if file is not None:
        data = await file.read()
    elif name in DOCUMENTS:
        data = DOCUMENTS[name][1]()
    else:
        raise FVError(INVALID_REQUEST, detail="send a file or a demo document name")
    limiter.take(sid, "agent", settings.agent_asks_per_session, AGENT_QUESTION_LIMIT_REACHED)
    if kind == "po":
        return {"kind": kind, **await run_in_threadpool(extract.read_customer_po, data)}
    if kind == "vendor_email":
        return {"kind": kind, **await run_in_threadpool(extract.read_vendor_email, data.decode("utf-8", "replace"))}
    raise FVError(INVALID_REQUEST, detail={"kind": kind, "valid": ["po", "vendor_email"]})

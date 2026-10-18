"""Customer Orders (module 0): list, detail, create, cancel and source documents.

A person enters or cancels an order here, so the action is recorded in the Action Center and approved in the same
step by that person; an agent never reaches these routes.
"""

import base64
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import actions, effects, modules, plans
from app.errors import FVError
from app.session import SessionId, user_label
from app.store import store
from app.tools import orders

INVALID_REQUEST = "FV-REQ-001"
UNKNOWN_ENTITY = "FV-DATA-001"
NOT_CANCELLABLE = "FV-ORD-001"
INVALID_COLOR = "FV-MAT-003"

DOCS = "order_documents"
MAX_DOC_BYTES = 700_000  # base64 of this stays under the 1 MiB Firestore document limit
MAX_DOCS_PER_ORDER = 10
DOC_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/csv",
    "message/rfc822",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

router = APIRouter(prefix="/api/orders")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class OrderLineIn(BaseModel):
    sku: str = ""
    color: str | None = None
    qty: int | None = None
    requested_delivery_date: str | None = None


class OrderIn(BaseModel):
    customer_id: str = ""
    order_channel: str = ""
    po_number: str | None = None
    order_date: str | None = None
    requested_delivery_date: str | None = None
    customer_pic: str | None = None
    delivery_address: str | None = None
    dock_number: str | None = None
    lines: list[OrderLineIn] = Field(default_factory=list, max_length=20)


class CancelIn(BaseModel):
    reason: str = ""


def _documents(sid: str, order_number: str) -> list[dict[str, Any]]:
    docs = [d for d in store().where(DOCS, "session_id", sid) if d["order_number"] == order_number]
    return sorted(({k: v for k, v in d.items() if k != "content_b64"} for d in docs), key=lambda d: str(d["uploaded_at"]))


def _detail(sid: str, o: effects.Overrides, order_number: str) -> dict[str, Any]:
    h = orders.find_order(plans.order_view(sid, o), order_number)
    return {
        **h,
        "traceability": orders.traceability(h, o.demand_change_pct or None),
        "documents": _documents(sid, order_number),
        "production_plans": plans.plans_for_order(sid, order_number),
    }


def _checked(body: OrderIn, o: effects.Overrides) -> tuple[dict[str, Any], list[str]]:
    data = body.model_dump()
    errors, warnings = orders.validate_order(data, modules.order_view(o))
    if any(e["field"].endswith(".color") for e in errors):
        raise FVError(INVALID_COLOR, "A colour on the order is not made for its product.", {"fields": errors})
    if errors:
        raise FVError(INVALID_REQUEST, "The order has fields to fix.", {"fields": errors})
    return data, warnings


@router.get("")
def list_orders(
    sid: SessionId,
    status: str | None = None,
    customer_id: str | None = None,
    sku: str | None = None,
    delivery_from: str | None = None,
    delivery_to: str | None = None,
) -> dict[str, Any]:
    view = plans.order_view(sid)
    rows = orders.filter_orders(view, status, customer_id, sku, delivery_from, delivery_to)
    return {
        **{k: v for k, v in view.items() if k != "orders"},
        "orders": rows,
        "total": len(view["orders"]),
        "customers": orders.customers(),
        "products": orders.products(),
        "channels": orders.channels(),
    }


@router.post("/validate")
def validate(body: OrderIn, sid: SessionId) -> dict[str, Any]:
    """The wizard's review step: field errors, warnings, and when the order would complete at current capacity."""
    o = effects.overrides(sid)
    data = body.model_dump()
    errors, warnings = orders.validate_order(data, modules.order_view(o))
    preview = None
    if not errors:
        draft = orders.new_order(data, "preview", _now())
        trial = effects.Overrides(**{**o.as_dict(), "new_orders": [*o.new_orders, draft]})
        h = orders.find_order(plans.order_view(sid, trial), draft["order_number"])
        preview = {k: h[k] for k in ("status", "at_risk", "estimated_completion", "days_late", "penalty_idr")}
        if h["at_risk"]:
            warnings.append(f"At current capacity this order completes on {h['estimated_completion']}, {h['days_late']} day(s) after the requested delivery.")
    return {"errors": errors, "warnings": warnings, "preview": preview}


@router.post("")
def create_order(body: OrderIn, request: Request, sid: SessionId) -> dict[str, Any]:
    o = effects.overrides(sid)
    data, warnings = _checked(body, o)
    user = user_label(request)
    order = orders.new_order(data, user, _now())
    qty = sum(ln["order_qty"] for ln in order["lines"])
    customer = orders.CUSTOMER_BY_ID[order["customer_id"]]["customer_name"]
    a = actions.propose(
        sid,
        "customer_order",
        f"Record {customer} order {order['order_number']}: {len(order['lines'])} line(s), {qty:,} pcs",
        "manual_entry",
        f"Entered by {user} on the Customer Orders page ({orders.CHANNEL_LABEL[order['order_channel']]}). A person entered it, so it is recorded and approved in one step.",
        {"order": order},
        evidence=[{"source": "Customer Orders page, manual entry", "warnings": warnings}],
    )
    actions.decide(sid, a["action_id"], approve=True, user=user, note="entered by hand")
    return {"order": _detail(sid, effects.overrides(sid), order["order_number"]), "warnings": warnings, "action_id": a["action_id"]}


@router.get("/{order_number}")
def get_order(order_number: str, sid: SessionId) -> dict[str, Any]:
    return _detail(sid, effects.overrides(sid), order_number)


@router.post("/{order_number}/cancel")
def cancel_order(order_number: str, body: CancelIn, request: Request, sid: SessionId) -> dict[str, Any]:
    o = effects.overrides(sid)
    h = orders.find_order(plans.order_view(sid, o), order_number)
    reason = body.reason.strip()
    if len(reason) < 3:
        raise FVError(
            INVALID_REQUEST, "A cancellation reason is required.", {"fields": [{"field": "reason", "message": "Give a reason of at least 3 characters."}]}
        )
    if h["cancel_block_reason"]:
        raise FVError(NOT_CANCELLABLE, h["cancel_block_reason"], {"order_number": order_number, "status": h["status"]})
    user = user_label(request)
    a = actions.propose(
        sid,
        "order_cancel",
        f"Cancel {h['customer_name']} order {order_number}",
        "manual_entry",
        f"Cancelled by {user}: {reason}",
        {"order_number": order_number, "reason": reason},
        evidence=[{"source": "Customer Orders page, manual entry", "status_before": h["status"]}],
    )
    actions.decide(sid, a["action_id"], approve=True, user=user, note=reason)
    return {"order": _detail(sid, effects.overrides(sid), order_number), "action_id": a["action_id"]}


@router.post("/{order_number}/documents")
async def attach_document(order_number: str, sid: SessionId, request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    orders.find_order(modules.order_view(effects.overrides(sid)), order_number)
    data = await file.read()
    mime = (file.content_type or "").split(";")[0]
    problem = None
    if mime not in DOC_TYPES:
        problem = "Attach a PDF, image, text, CSV, email (.eml) or Excel file."
    elif not data:
        problem = "The file is empty."
    elif len(data) > MAX_DOC_BYTES:
        problem = f"The file is {len(data) // 1024} KB; the limit is {MAX_DOC_BYTES // 1000} KB."
    elif len(_documents(sid, order_number)) >= MAX_DOCS_PER_ORDER:
        problem = f"An order keeps at most {MAX_DOCS_PER_ORDER} documents."
    if problem:
        raise FVError(INVALID_REQUEST, problem, {"fields": [{"field": "file", "message": problem}]})
    doc: dict[str, Any] = {
        "doc_id": f"DOC-{uuid.uuid4().hex[:10]}",
        "session_id": sid,
        "order_number": order_number,
        "file_name": re.sub(r"[^\w.\- ]", "_", file.filename or "document")[:200],
        "content_type": mime,
        "size_bytes": len(data),
        "uploaded_at": _now(),
        "uploaded_by": user_label(request),
    }
    store().set(DOCS, doc["doc_id"], {**doc, "content_b64": base64.b64encode(data).decode()})
    return doc


@router.get("/{order_number}/documents/{doc_id}")
def get_document(order_number: str, doc_id: str, sid: SessionId) -> Response:
    d = store().get(DOCS, doc_id)
    if not d or d["session_id"] != sid or d["order_number"] != order_number:
        raise FVError(UNKNOWN_ENTITY, f"unknown document {doc_id}")
    return Response(base64.b64decode(d["content_b64"]), media_type=d["content_type"], headers={"Content-Disposition": f'inline; filename="{d["file_name"]}"'})

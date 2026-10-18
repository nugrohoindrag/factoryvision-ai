from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import purchasing_docs as pd
from app.session import SessionId

router = APIRouter(prefix="/api/purchasing")

User = Annotated[str | None, Header(alias="X-User")]


def _user(x_user: str | None) -> str:
    return (x_user or "").strip()[:80] or "user"


class LineIn(BaseModel):
    material_id: str = ""
    qty: float | None = None
    need_by: str | None = None
    suggested_vendor_id: str | None = None
    source: str | None = None
    note: str | None = None


class RequisitionIn(BaseModel):
    lines: list[LineIn] | None = Field(default=None, max_length=100)
    from_mrp_run: str | None = None
    from_plan: str | None = None
    material_ids: list[str] | None = None
    note: str | None = None


class RequisitionPatch(BaseModel):
    lines: list[LineIn] | None = Field(default=None, max_length=100)
    note: str | None = None


class ReasonIn(BaseModel):
    reason: str | None = None


class ConvertIn(BaseModel):
    vendor_overrides: dict[str, str] | None = None


class OrderIn(BaseModel):
    vendor_id: str = ""
    lines: list[LineIn] = Field(default_factory=list, max_length=100)
    promised_date: str | None = None
    pr_id: str | None = None
    note: str | None = None
    issue: bool = False


class OrderPatch(BaseModel):
    lines: list[LineIn] | None = Field(default=None, max_length=100)
    promised_date: str | None = None
    note: str | None = None


class ExpediteIn(BaseModel):
    new_promised_date: str = ""
    reason: str = ""


class ReceiptLineIn(BaseModel):
    material_id: str = ""
    qty: float | None = None


class ReceiptIn(BaseModel):
    lines: list[ReceiptLineIn] = Field(default_factory=list, max_length=100)
    received_date: str | None = None
    lot_id: str | None = None
    note: str | None = None


def _lines(lines: list[LineIn] | list[ReceiptLineIn] | None) -> list[dict[str, Any]] | None:
    return None if lines is None else [ln.model_dump(exclude_none=True) for ln in lines]


@router.get("/vendors")
def list_vendors(sid: SessionId) -> dict[str, Any]:
    return {"vendors": pd.list_vendors(sid=sid)}


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: str, sid: SessionId) -> dict[str, Any]:
    return pd.get_vendor(vendor_id, sid=sid)


@router.get("/recommend")
def recommend(sid: SessionId, material_id: str, qty: float, need_by: str | None = None) -> dict[str, Any]:
    return pd.recommend(material_id, qty, need_by or None, sid=sid)


@router.get("/requisitions")
def list_requisitions(sid: SessionId, status: str | None = None) -> dict[str, Any]:
    return {"requisitions": pd.list_requisitions(sid, status or None)}


@router.post("/requisitions")
def create_requisition(body: RequisitionIn, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.create_requisition(
        sid,
        lines=_lines(body.lines),
        from_mrp_run=body.from_mrp_run or None,
        from_plan=body.from_plan or None,
        material_ids=body.material_ids or None,
        note=body.note,
        user=_user(x_user),
    )


@router.get("/requisitions/{pr_id}")
def get_requisition(pr_id: str, sid: SessionId) -> dict[str, Any]:
    return pd.get_requisition(sid, pr_id)


@router.patch("/requisitions/{pr_id}")
def update_requisition(pr_id: str, body: RequisitionPatch, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.update_requisition(sid, pr_id, _lines(body.lines), _user(x_user), note=body.note)


@router.post("/requisitions/{pr_id}/convert")
def convert_requisition(pr_id: str, sid: SessionId, x_user: User = None, body: ConvertIn | None = None) -> dict[str, Any]:
    return pd.convert_requisition(sid, pr_id, vendor_overrides=(body or ConvertIn()).vendor_overrides, user=_user(x_user))


@router.post("/requisitions/{pr_id}/{action}")
def transition_requisition(pr_id: str, action: str, sid: SessionId, x_user: User = None, body: ReasonIn | None = None) -> dict[str, Any]:
    return pd.transition_requisition(sid, pr_id, action, user=_user(x_user), reason=(body or ReasonIn()).reason)


@router.get("/orders")
def list_orders(
    sid: SessionId, status: str | None = None, vendor_id: str | None = None, material_id: str | None = None, overdue: bool | None = None
) -> dict[str, Any]:
    return {"orders": pd.list_pos(sid, status or None, vendor_id or None, material_id or None, overdue)}


@router.post("/orders")
def create_order(body: OrderIn, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.create_po(
        sid,
        vendor_id=body.vendor_id,
        lines=_lines(body.lines) or [],
        promised_date=body.promised_date or None,
        pr_id=body.pr_id or None,
        note=body.note,
        user=_user(x_user),
        issue=body.issue,
    )


@router.get("/orders/{po_id}")
def get_order(po_id: str, sid: SessionId) -> dict[str, Any]:
    return pd.get_po(sid, po_id)


@router.patch("/orders/{po_id}")
def update_order(po_id: str, body: OrderPatch, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.update_po(sid, po_id, lines=_lines(body.lines), promised_date=body.promised_date, note=body.note, user=_user(x_user))


@router.get("/orders/{po_id}/document", response_class=PlainTextResponse)
def order_document(po_id: str, sid: SessionId) -> PlainTextResponse:
    return PlainTextResponse(pd.po_document(sid, po_id), headers={"Content-Disposition": f'inline; filename="{po_id}.txt"'})


@router.get("/orders/{po_id}/receipts")
def order_receipts(po_id: str, sid: SessionId) -> dict[str, Any]:
    pd.get_po(sid, po_id)
    return {"receipts": pd.list_receipts(sid, po_id)}


@router.post("/orders/{po_id}/receipts")
def receive(po_id: str, body: ReceiptIn, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.receive(
        sid, po_id, _lines(body.lines) or [], received_date=body.received_date or None, lot_id=body.lot_id or None, note=body.note, user=_user(x_user)
    )


@router.post("/orders/{po_id}/expedite")
def expedite(po_id: str, body: ExpediteIn, sid: SessionId, x_user: User = None) -> dict[str, Any]:
    return pd.expedite_po(sid, po_id, body.new_promised_date, body.reason, user=_user(x_user))


@router.post("/orders/{po_id}/{action}")
def transition_order(po_id: str, action: str, sid: SessionId, x_user: User = None, body: ReasonIn | None = None) -> dict[str, Any]:
    return pd.transition_po(sid, po_id, action, user=_user(x_user), reason=(body or ReasonIn()).reason)


@router.get("/receipts")
def list_receipts(sid: SessionId, po_id: str | None = None) -> dict[str, Any]:
    return {"receipts": pd.list_receipts(sid, po_id or None)}


@router.get("/summary")
def summary(sid: SessionId) -> dict[str, Any]:
    return pd.summary(sid)

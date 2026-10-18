"""Work Orders and the Production Board (module 3): the shop-floor side of the plans' work orders."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import effects, plant, twin
from app import work_orders as wo
from app.session import SessionId, user_label

router = APIRouter(prefix="/api")


def floor(sid: str) -> wo.Floor:
    """The plant clock and machine states come from this session's digital twin (07:00 before it first ticks)."""
    s = twin.find(sid)
    return wo.Floor.of(
        effects.overrides(sid),
        now=plant.DEMO_START + timedelta(minutes=s.minute if s else 0),
        machine_status={mid: m.status for mid, m in s.machines.items()} if s else {},
    )


class TransitionIn(BaseModel):
    to: str
    reason: str = Field(default="", max_length=500)


class OutputIn(BaseModel):
    output_qty: int = 0
    reject_qty: int = 0
    scrap_qty: int = 0


class PartIn(BaseModel):
    qty: int
    machine_id: str | None = None


class SplitIn(BaseModel):
    parts: list[PartIn]


class DispatchIn(BaseModel):
    action: str
    wo_id: str
    planned_start: str | None = None
    planned_end: str | None = None
    machine_id: str | None = None
    priority: int | None = None
    reason: str = Field(default="", max_length=500)


@router.get("/work-orders")
def list_work_orders(sid: SessionId, status: str | None = None, process: str | None = None, plan_id: str | None = None, q: str | None = None) -> dict[str, Any]:
    return wo.list_work_orders(sid, floor(sid), status=status or None, process=process or None, plan_id=plan_id or None, q=q or None)


@router.get("/work-orders/{wo_id}")
def get_work_order(wo_id: str, sid: SessionId) -> dict[str, Any]:
    return wo.detail(sid, wo_id, floor(sid))


@router.post("/work-orders/{wo_id}/transition")
def transition(wo_id: str, body: TransitionIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return wo.transition(sid, wo_id, body.to, reason=body.reason, user=user_label(request), floor=floor(sid))


@router.post("/work-orders/{wo_id}/output")
def report_output(wo_id: str, body: OutputIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return wo.report_output(
        sid, wo_id, output_qty=body.output_qty, reject_qty=body.reject_qty, scrap_qty=body.scrap_qty, user=user_label(request), floor=floor(sid)
    )


@router.post("/work-orders/{wo_id}/split")
def split(wo_id: str, body: SplitIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return wo.split(sid, wo_id, [p.model_dump() for p in body.parts], user=user_label(request), floor=floor(sid))


@router.get("/production-board")
def production_board(
    sid: SessionId, view: str = "machine", date: str | None = None, days: int = 1, process: str | None = None, sku: str | None = None
) -> dict[str, Any]:
    return wo.board(sid, floor(sid), view=view, day=date or None, days=days, process=process or None, sku=sku or None)


@router.post("/production-board/dispatch")
def dispatch(body: DispatchIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return wo.dispatch(sid, body.model_dump(), user=user_label(request), floor=floor(sid))

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import actions, effects, modules
from app.briefing import briefing
from app.errors import FVError
from app.session import SessionId, user_label
from app.tools import orders

INVALID_REQUEST = "FV-REQ-001"

router = APIRouter(prefix="/api")


@router.get("/briefing")
def get_briefing(_sid: SessionId) -> dict[str, Any]:
    return briefing()


@router.get("/modules/planning/what-if")
def planning_what_if(sid: SessionId) -> dict[str, Any]:
    return modules.what_if(effects.overrides(sid))


@router.get("/modules/{module}/summary")
def module_summary(module: str, sid: SessionId) -> dict[str, Any]:
    return modules.summary(module, effects.overrides(sid))


@router.get("/forecast/{sku}")
def forecast_detail(sku: str, sid: SessionId) -> dict[str, Any]:
    return modules.forecast_detail(sku, effects.overrides(sid))


class PoLine(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    month: str | None = None
    previous_qty: int | None = None


class CustomerPo(BaseModel):
    customer_name: str
    po_number: str | None = None
    issue_date: str | None = None
    notes: str | None = None
    lines: list[PoLine] = Field(min_length=1, max_length=50)


@router.post("/orders/po/propose")
def propose_po(body: CustomerPo, sid: SessionId) -> dict[str, Any]:
    """Turn a read PO into a pending order change; the numbers are re-checked here, never taken from the client."""
    check = orders.check_po(body.model_dump())
    if not check["changed"]:
        raise FVError(INVALID_REQUEST, detail="the PO changes no quantity against the schedule on file")
    head = ", ".join(f"{x['sku']} {x['change_pct']:+g}%" for x in check["changed"])
    return actions.propose(
        sid,
        "customer_po",
        f"Accept {check['customer_name']} PO {check['po_number'] or ''}: {head}".replace("  ", " "),
        "customer_order_intake",
        f"Revised customer schedule for {check['lines'][0]['month']}. Approving updates demand for planning, material and purchasing.",
        {"customer_id": check["customer_id"], "po_number": check["po_number"], "lines": check["changed"]},
        evidence=[{"source": check["source"], "lines": check["lines"], "warnings": check["warnings"]}],
    )


@router.get("/actions")
def list_actions(sid: SessionId, status: str | None = None) -> dict[str, Any]:
    items = actions.list_actions(sid, status)
    plans: dict[str, list[str]] = {}
    for a in items:
        if a["plan_id"]:
            plans.setdefault(a["plan_id"], []).append(a["action_id"])
    return {"actions": items, "plans": plans, "pending": sum(1 for a in items if a["status"] == "pending")}


class Decision(BaseModel):
    note: str | None = None


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, request: Request, sid: SessionId, body: Decision | None = None) -> dict[str, Any]:
    return actions.decide(sid, action_id, approve=True, user=user_label(request), note=(body or Decision()).note)


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, request: Request, sid: SessionId, body: Decision | None = None) -> dict[str, Any]:
    return actions.decide(sid, action_id, approve=False, user=user_label(request), note=(body or Decision()).note)


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return actions.decide_plan(sid, plan_id, approve=True, user=user_label(request))


@router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return actions.decide_plan(sid, plan_id, approve=False, user=user_label(request))

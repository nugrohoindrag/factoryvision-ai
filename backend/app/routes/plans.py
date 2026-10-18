from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import actions, capacity, plans
from app.session import SessionId, user_label
from app.tools.common import TODAY

router = APIRouter(prefix="/api")


class PlanIn(BaseModel):
    period_start: str | None = None
    period_end: str | None = None


class DraftIn(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    include_forecast: bool = True


class StepIn(BaseModel):
    step: int


class DemandIn(BaseModel):
    order_id: str
    qty: int | None = None


class ForecastDemandIn(BaseModel):
    sku: str
    qty: int | None = None


class LineIn(BaseModel):
    planned_qty: int | None = None
    priority: int | None = None


class WorkOrderIn(BaseModel):
    machine_id: str | None = None
    mold_id: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    shift: str | None = None


class WorkOrderStatusIn(BaseModel):
    status: str


class CancelIn(BaseModel):
    reason: str = ""


class RecalculateIn(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    snapshot_id: str | None = None
    planning_utilization_pct: float | None = None


class SettingsIn(BaseModel):
    planning_utilization_pct: float


@router.get("/plans")
def list_plans(sid: SessionId, status: str | None = None, period_from: str | None = None, period_to: str | None = None) -> dict[str, Any]:
    return {"plans": plans.list_plans(sid, status or None, period_from or None, period_to or None), "as_of": TODAY.isoformat()}


@router.post("/plans")
def create_plan(body: PlanIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return plans.create_plan(sid, body.period_start, body.period_end, user=user_label(request))


@router.post("/plans/draft")
def preview_draft(body: DraftIn, sid: SessionId) -> dict[str, Any]:
    """The planning agent's draft for a period, computed by its draft tool; nothing is saved."""
    return plans.draft(sid, body.period_start, body.period_end, include_forecast=body.include_forecast)


@router.post("/plans/draft/propose")
def propose_draft(body: DraftIn, sid: SessionId) -> dict[str, Any]:
    """Send the agent's draft to the Action Center; after approval it becomes a Draft plan the planner finishes in the wizard."""
    d = plans.draft(sid, body.period_start, body.period_end, include_forecast=body.include_forecast)
    a = actions.propose(
        sid,
        "production_plan_draft",
        f"Draft production plan {d['period_start']} to {d['period_end']}: {len(d['lines'])} line(s), {d['summary']['planned_qty']:,} pcs",
        "planning",
        plans.draft_rationale(d),
        d["args"],
        evidence=[{"source": d["source"], "rules": d["rules"], "lines": d["lines"], "gaps": d["gaps"]}],
    )
    return {"action": a, "draft": d}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return plans.get_plan(sid, plan_id)


@router.post("/plans/{plan_id}/step")
def set_step(plan_id: str, body: StepIn, sid: SessionId) -> dict[str, Any]:
    return plans.set_step(sid, plan_id, body.step)


@router.get("/plans/{plan_id}/demand-candidates")
def demand_candidates(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return {"candidates": plans.demand_candidates(sid, plan_id)}


@router.get("/plans/{plan_id}/demand")
def demand_breakdown(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return {"lines": plans.demand_breakdown(sid, plan_id)}


@router.post("/plans/{plan_id}/demand")
def add_demand(plan_id: str, body: DemandIn, sid: SessionId) -> dict[str, Any]:
    return plans.add_demand(sid, plan_id, body.order_id, body.qty)


@router.delete("/plans/{plan_id}/demand/{order_id}")
def remove_demand(plan_id: str, order_id: str, sid: SessionId) -> dict[str, Any]:
    return plans.remove_demand(sid, plan_id, order_id)


@router.post("/plans/{plan_id}/forecast-demand")
def forecast_demand(plan_id: str, body: ForecastDemandIn, sid: SessionId) -> dict[str, Any]:
    return plans.set_forecast_demand(sid, plan_id, body.sku, body.qty)


@router.patch("/plans/{plan_id}/lines/{line_id}")
def update_line(plan_id: str, line_id: str, body: LineIn, sid: SessionId) -> dict[str, Any]:
    return plans.update_line(sid, plan_id, line_id, body.planned_qty, body.priority)


@router.post("/plans/{plan_id}/work-orders")
def generate_work_orders(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return plans.generate_work_orders(sid, plan_id)


@router.post("/plans/{plan_id}/schedule")
def schedule(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return plans.schedule(sid, plan_id)


@router.post("/plans/{plan_id}/work-orders/confirm")
def confirm_work_orders(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return plans.confirm_work_orders(sid, plan_id)


@router.patch("/plans/{plan_id}/work-orders/{wo_id}")
def update_work_order(plan_id: str, wo_id: str, body: WorkOrderIn, sid: SessionId) -> dict[str, Any]:
    return plans.update_work_order(sid, plan_id, wo_id, body.model_dump(exclude_unset=True))


@router.post("/plans/{plan_id}/work-orders/{wo_id}/status")
def advance_work_order(plan_id: str, wo_id: str, body: WorkOrderStatusIn, sid: SessionId) -> dict[str, Any]:
    return plans.advance_work_order(sid, plan_id, wo_id, body.status)


@router.post("/plans/{plan_id}/confirm")
def confirm(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return plans.confirm(sid, plan_id, user_label(request))


@router.post("/plans/{plan_id}/cancel")
def cancel(plan_id: str, body: CancelIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return plans.cancel(sid, plan_id, body.reason, user_label(request))


@router.get("/capacity")
def get_capacity(
    sid: SessionId, period_start: str | None = None, period_end: str | None = None, planning_utilization_pct: float | None = None
) -> dict[str, Any]:
    return plans.capacity_page(sid, period_start or None, period_end or None, planning_utilization_pct)


@router.post("/capacity/recalculate")
def recalculate(request: Request, sid: SessionId, body: RecalculateIn | None = None) -> dict[str, Any]:
    b = body or RecalculateIn()
    return plans.recalculate_capacity(sid, b.period_start, b.period_end, b.snapshot_id, b.planning_utilization_pct, user_label(request))


@router.get("/capacity/snapshots")
def list_snapshots(sid: SessionId) -> dict[str, Any]:
    return {"snapshots": capacity.snapshots(sid)}


@router.get("/capacity/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, sid: SessionId) -> dict[str, Any]:
    return capacity.get_snapshot(sid, snapshot_id)


@router.get("/capacity/settings")
def get_settings(sid: SessionId) -> dict[str, Any]:
    return capacity.settings(sid)


@router.put("/capacity/settings")
def put_settings(body: SettingsIn, sid: SessionId) -> dict[str, Any]:
    return capacity.set_utilization_pct(sid, body.planning_utilization_pct)

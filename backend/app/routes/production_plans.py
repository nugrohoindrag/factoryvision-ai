"""Production Plan (module 2): plans built by a planner in the six-step wizard, per session.

A person edits the plan here, so changes apply to the session's plan at once; an agent's re-plan reaches the plan only
as a proposal in the Action Center.
"""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import effects, modules
from app.effects import normal_cycle_factor
from app.session import SessionId, user_label
from app.store import Doc
from app.tools import planning
from app.tools import production_plan as pp
from app.tools.common import TODAY, to_date

router = APIRouter(prefix="/api/production-plans")


class PlanIn(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    forecast_id: str | None = None
    capacity_plan_id: str | None = None


class PlanPatch(PlanIn):
    wizard_step: int | None = None
    wizard_state: dict[str, Any] | None = None


class DemandIn(BaseModel):
    order_id: str
    demand_qty: int | None = None


class ForecastDemandIn(BaseModel):
    sku: str
    forecast_qty: int | None = None


class LineIn(BaseModel):
    planned_qty: int | None = None
    priority: int | None = None


class ResourceIn(BaseModel):
    machine_id: str | None = None
    mold_id: str | None = None


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)


def _rates(o: effects.Overrides) -> dict[str, dict[str, Any]]:
    """Actual cycle and yield of the last 7 days; a machine under approved maintenance runs at the fleet's normal cycle."""
    rates = planning.machine_rates()
    if o.maintenance:
        normal = normal_cycle_factor(rates)
        rates = {mid: ({**r, "cycle_factor": normal} if mid in o.maintenance else r) for mid, r in rates.items()}
    return rates


def _out(sid: str, plan: Doc, notes: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Save, then answer with the plan as the wizard shows it: the capacity of every line is recomputed here."""
    o = effects.overrides(sid)
    view = modules.order_view(o)
    if plan["status"] in pp.EDITABLE:
        capacity = pp.apply_assessment(plan, view, _rates(o))
        pp.refresh_status(plan)
    else:
        capacity = None
    pp.save(plan)
    view = modules.order_view(effects.overrides(sid))
    return {
        "plan": pp.detail(plan),
        "capacity": capacity,
        "candidates": pp.candidates(plan, view) if plan["status"] in pp.EDITABLE else [],
        "notes": notes or [],
        **extra,
    }


def _baseline(o: effects.Overrides, plan_id: str) -> dict[str, Any] | None:
    return next((p for p in pp.baseline_plans(o.demand_change_pct or None) if p["plan_id"] == plan_id), None)


@router.get("")
def list_plans(sid: SessionId, status: str | None = None, period_from: str | None = None, period_to: str | None = None) -> dict[str, Any]:
    o = effects.overrides(sid)
    mine = sorted(pp.session_plans(sid), key=lambda p: str(p["created_at"]), reverse=True)
    rows = [pp.header(p) for p in mine] + [
        pp.header(p) for p in sorted(pp.baseline_plans(o.demand_change_pct or None), key=lambda p: p["period_start"], reverse=True)
    ]
    total = len(rows)
    if status:
        rows = [r for r in rows if r["status"] == status]
    if period_from:
        rows = [r for r in rows if r["period_end"] >= period_from]
    if period_to:
        rows = [r for r in rows if r["period_start"] <= period_to]
    return {
        "as_of": TODAY.isoformat(),
        "plans": rows,
        "total": total,
        "statuses": list(pp.STATUSES),
        "capacity_statuses": list(pp.CAPACITY_STATUSES),
        "default_utilization_pct": pp.DEFAULT_UTILIZATION_PCT,
        "steps": pp.STEP_LABELS,
    }


@router.post("")
def create_plan(body: PlanIn, request: Request, sid: SessionId) -> dict[str, Any]:
    plan = pp.new_plan(sid, body.model_dump(), user_label(request))
    return _out(sid, plan)


@router.get("/{plan_id}")
def get_plan(plan_id: str, sid: SessionId) -> dict[str, Any]:
    o = effects.overrides(sid)
    base = _baseline(o, plan_id)
    if base is not None:
        return {
            "plan": {**base, "editable": False, "locked_reason": "Recorded before this session from the OEM monthly schedule; read-only."},
            "capacity": None,
            "candidates": [],
            "notes": [],
        }
    return _out(sid, pp.load(sid, plan_id))


@router.patch("/{plan_id}")
def patch_plan(plan_id: str, body: PlanPatch, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    notes = pp.patch_plan(plan, body.model_dump())
    return _out(sid, plan, notes)


@router.post("/{plan_id}/demands")
def add_demand(plan_id: str, body: DemandIn, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    demand = pp.add_demand(plan, modules.order_view(effects.overrides(sid)), body.order_id, body.demand_qty)
    return _out(sid, plan, demand=demand)


@router.delete("/{plan_id}/demands/{demand_id}")
def remove_demand(plan_id: str, demand_id: str, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    pp.remove_demand(plan, demand_id)
    return _out(sid, plan)


@router.get("/{plan_id}/forecast/{sku}")
def forecast_preview(plan_id: str, sku: str, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    o = effects.overrides(sid)
    return pp.forecast_open_qty(sku, to_date(plan["period_start"]), to_date(plan["period_end"]), o.demand_change_pct or None)


@router.post("/{plan_id}/forecast-demand")
def forecast_demand(plan_id: str, body: ForecastDemandIn, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    fc = pp.set_forecast_demand(plan, body.sku, body.forecast_qty, effects.overrides(sid).demand_change_pct or None)
    return _out(sid, plan, forecast=fc)


@router.patch("/{plan_id}/lines/{line_id}")
def update_line(plan_id: str, line_id: str, body: LineIn, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    dropped = pp.update_line(plan, line_id, body.planned_qty, body.priority)
    notes = ["The planned quantity changed, so this line's work orders were removed. Generate them again in step 3."] if dropped else []
    return _out(sid, plan, notes)


@router.post("/{plan_id}/work-orders/generate")
def generate_work_orders(plan_id: str, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    taken = {w["wo_id"] for p in pp.session_plans(sid) for w in p["work_orders"]}
    result = pp.generate_work_orders(plan, taken)
    return _out(sid, plan, generated=result)


@router.post("/{plan_id}/schedule")
def schedule(plan_id: str, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    o = effects.overrides(sid)
    run = pp.schedule(plan, modules.order_view(o), _rates(o))
    return _out(sid, plan, schedule=run)


@router.patch("/{plan_id}/work-orders/{wo_id}")
def set_resource(plan_id: str, wo_id: str, body: ResourceIn, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    notes = pp.set_resource(plan, wo_id, body.machine_id, body.mold_id)
    return _out(sid, plan, notes)


@router.post("/{plan_id}/work-orders/confirm")
def confirm_work_orders(plan_id: str, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    n = pp.confirm_work_orders(plan)
    return _out(sid, plan, [f"{n} work order(s) confirmed."] if n else [])


@router.post("/{plan_id}/confirm")
def confirm_plan(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    o = effects.overrides(sid)
    pp.apply_assessment(plan, modules.order_view(o), _rates(o))  # confirm judges capacity as it is now
    pp.confirm(plan, user_label(request))
    return _out(sid, plan)


@router.post("/{plan_id}/cancel")
def cancel_plan(plan_id: str, body: CancelIn, request: Request, sid: SessionId) -> dict[str, Any]:
    plan = pp.load(sid, plan_id)
    pp.cancel(plan, body.reason, user_label(request))
    return _out(sid, plan)

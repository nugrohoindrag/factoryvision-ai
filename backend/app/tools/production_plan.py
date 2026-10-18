"""Production Plan (module 2): the plans a planner builds in a six-step wizard, kept per session.

1 Demand → 2 Production Plan → 3 Work Order → 4 Scheduling → 5 Resource → 6 Confirmation.

A plan line aggregates the demand for one SKU, while every order line behind it keeps its own demand row, so the
order a piece is made for never gets lost. The capacity status of a line is always computed here, never taken from
the client. Molding work orders are scheduled by OR-Tools CP-SAT; painting and assembly follow molding.

The plans recorded before the session (one per month of the OEM schedule, e.g. PP-2027-02) are listed read-only.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

from ortools.sat.python import cp_model

from app import plant, warehouse
from app.errors import FVError
from app.store import Doc, store
from app.tools import forecast, orders, planning
from app.tools.common import AVAILABLE_FACTOR, MINUTES_PER_WORKDAY, TODAY, pct, to_date, working_days_between

INVALID_REQUEST = "FV-REQ-001"
UNKNOWN_ENTITY = "FV-DATA-001"
PLAN_LOCKED = "FV-PLAN-002"
STEP_NOT_READY = "FV-PLAN-003"
DEMAND_NOT_PLANNABLE = "FV-PLAN-004"
NO_FEASIBLE_PLAN = "FV-PLAN-001"

COLLECTION = "production_plans"
STATUSES = ("Draft", "Planning", "Ready", "Confirmed", "In Execution", "Completed", "Cancelled")
EDITABLE = ("Draft", "Planning", "Ready")
CAPACITY_STATUSES = ("Within Plan", "Additional Demand", "Capacity Up Required")
DEFAULT_UTILIZATION_PCT = 80.0  # planning capacity = total x this; the rest is buffer for disruptions (capacity-planning.md §2)
MAX_PERIOD_DAYS = 62
STEP_LABELS = {1: "Demand", 2: "Production Plan", 3: "Work Order", 4: "Scheduling", 5: "Resource", 6: "Confirmation"}

# Routing of every bumper: molding -> painting -> assembly. Trimming, wash and curing run inline with the next process.
PROCESSES = (("MLD", "Injection Molding"), ("PNT", "Painting"), ("ASM", "Assembly"))
PAINT_LINE = "PNT-01"  # the primer booth feeds the basecoat booth and the oven on one conveyor
ASSEMBLY_CELLS = ("ASM-01", "ASM-02", "ASM-03")
PAINT_TAKT_S = 40.0  # planning assumption: one bumper leaves the paint line every 40 s
ASSEMBLY_TAKT_S = 75.0  # planning assumption: one bumper per 75 s per assembly cell
PAINT_LAG_MIN = 8 * 60  # painting starts one shift after molding (cooling and trimming buffer)
ASSEMBLY_LAG_MIN = 4 * 60
MOLD_LEAD_DAYS = 2  # molding finishes two days before delivery; painting and assembly take the day between
DAY_START_HOUR = 7  # production day runs 07:00 to 07:00 over shifts A, B, C


def _now() -> str:
    return datetime.now(plant.WIB).isoformat(timespec="seconds")


def _field_error(*, field: str, message: str, headline: str | None = None) -> FVError:
    return FVError(INVALID_REQUEST, headline or message, {"fields": [{"field": field, "message": message}]})


# --- storage ------------------------------------------------------------------------------------------------------


def session_plans(sid: str) -> list[Doc]:
    return store().where(COLLECTION, "session_id", sid)


def load(sid: str, plan_id: str) -> Doc:
    doc = store().get(COLLECTION, plan_id)
    if not doc or doc["session_id"] != sid:
        raise FVError(UNKNOWN_ENTITY, f"unknown production plan {plan_id}", {"plan_id": plan_id})
    return doc


def save(plan: Doc) -> None:
    plan["updated_at"] = _now()
    store().set(COLLECTION, plan["plan_id"], plan)


def clear(sid: str) -> None:
    for d in session_plans(sid):
        store().delete(COLLECTION, d["plan_id"])


def allocations(sid: str) -> dict[str, int]:
    """Quantity each customer order line has in this session's plans (cancelled plans give theirs back)."""
    out: dict[str, int] = {}
    for p in session_plans(sid):
        if p["status"] == "Cancelled":
            continue
        for d in p["demands"]:
            out[d["order_id"]] = out.get(d["order_id"], 0) + int(d["demand_qty"])
    return out


# --- plans recorded before the session ------------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _baseline(demand_key: str) -> str:
    demand = json.loads(demand_key) or None
    months = [to_date(r["plan_month"]) for r in warehouse.query("SELECT DISTINCT plan_month FROM fv.fact_monthly_plan ORDER BY plan_month")]
    molds = planning.current_molds()
    out = []
    for m in months:
        cap = planning.get_capacity(m.isoformat(), demand if m == TODAY.replace(day=1) else None)
        load = {x["machine_id"]: x["load_pct"] for x in cap["machines"]}
        end = (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        lines = []
        for k, (sku, qty) in enumerate(sorted(cap["plan_qty"].items())):
            mid = molds[sku]
            lines.append(
                {
                    "line_id": f"PP-{m:%Y-%m}-{sku}",
                    "sku": sku,
                    "product_name": orders.product_name(sku),
                    "demand_qty": qty,
                    "forecast_qty": 0,
                    "planned_qty": qty,
                    "priority": k + 1,
                    "machine_id": mid,
                    "load_pct": load[mid],
                    "capacity_status": _status(load[mid] / 100, 1.0, DEFAULT_UTILIZATION_PCT),
                    "status": "Released",
                }
            )
        out.append(
            {
                "plan_id": f"PP-{m:%Y-%m}",
                "plan_number": f"PP-{m:%Y-%m}",
                "baseline": True,
                "period_start": m.isoformat(),
                "period_end": end.isoformat(),
                "status": orders.plan_status(m),
                "planning_utilization_pct": DEFAULT_UTILIZATION_PCT,
                "created_by": "OEM monthly schedule",
                "created_at": None,
                "lines": lines,
                "demand_change_pct": demand if m == TODAY.replace(day=1) else {},
                "source": cap["source"],
            }
        )
    return json.dumps(out, default=str)


def baseline_plans(demand_change_pct: dict[str, float] | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = json.loads(_baseline(json.dumps(demand_change_pct or {}, sort_keys=True)))
    return result


# --- create and edit the header ---------------------------------------------------------------------------------------


def _date(v: object) -> date | None:
    try:
        return to_date(v) if v else None
    except ValueError:
        return None


def validate_period(start: object, end: object) -> tuple[date, date]:
    errors: list[dict[str, str]] = []
    s, e = _date(start), _date(end)
    if s is None:
        errors.append({"field": "period_start", "message": "Period start is required."})
    if e is None:
        errors.append({"field": "period_end", "message": "Period end is required."})
    elif s and e < s:
        errors.append({"field": "period_end", "message": "Period end must be on or after period start."})
    elif s and (e - s).days >= MAX_PERIOD_DAYS:
        errors.append({"field": "period_end", "message": f"A plan covers at most {MAX_PERIOD_DAYS} days."})
    elif e < TODAY:
        errors.append({"field": "period_end", "message": f"The period ends before today ({TODAY.isoformat()}); there is nothing left to plan."})
    if errors or s is None or e is None:
        raise FVError(INVALID_REQUEST, "The plan period has fields to fix.", {"fields": errors})
    return s, e


def next_number(start: date, taken: set[str]) -> str:
    """PP-2027-02 for the first plan of a month; PP-2027-02-002, -003 ... once that is taken (the OEM plan counts as 001)."""
    base = f"PP-{start:%Y-%m}"
    if base not in taken:
        return base
    n = 2 + sum(1 for t in taken if t.startswith(f"{base}-"))
    while f"{base}-{n:03d}" in taken:
        n += 1
    return f"{base}-{n:03d}"


def new_plan(sid: str, body: dict[str, Any], user: str) -> Doc:
    s, e = validate_period(body.get("period_start"), body.get("period_end"))
    taken = {p["plan_number"] for p in baseline_plans()} | {p["plan_number"] for p in session_plans(sid)}
    now = _now()
    return {
        "plan_id": f"plan-{uuid.uuid4().hex[:12]}",
        "session_id": sid,
        "plan_number": next_number(s, taken),
        "period_start": s.isoformat(),
        "period_end": e.isoformat(),
        "status": "Draft",
        "forecast_id": body.get("forecast_id") or None,
        "capacity_plan_id": body.get("capacity_plan_id") or None,
        # the utilization in force when the plan was created travels with it, so a later policy change does not restate it
        "planning_utilization_pct": float(body.get("planning_utilization_pct") or DEFAULT_UTILIZATION_PCT),
        "wizard_step": 1,
        "wizard_state": {},
        "lines": [],
        "demands": [],
        "work_orders": [],
        "schedule_run": None,
        "schedule_stale": False,
        "created_by": user,
        "created_at": now,
        "updated_at": now,
        "confirmed_by": None,
        "confirmed_at": None,
        "cancelled_by": None,
        "cancelled_at": None,
        "cancel_reason": None,
    }


def assert_editable(plan: Doc) -> None:
    if plan["status"] not in EDITABLE:
        raise FVError(
            PLAN_LOCKED,
            f"Production plan {plan['plan_number']} is {plan['status']} and can no longer change. A confirmed plan is a production commitment.",
            {"plan_id": plan["plan_id"], "status": plan["status"]},
        )


def patch_plan(plan: Doc, body: dict[str, Any]) -> list[str]:
    """Header edits while the plan is open. A new period drops the work orders, since their schedule depended on it."""
    assert_editable(plan)
    notes: list[str] = []
    if body.get("period_start") is not None or body.get("period_end") is not None:
        s, e = validate_period(body.get("period_start") or plan["period_start"], body.get("period_end") or plan["period_end"])
        if (s.isoformat(), e.isoformat()) != (plan["period_start"], plan["period_end"]):
            plan["period_start"], plan["period_end"] = s.isoformat(), e.isoformat()
            if plan["work_orders"]:
                notes.append("The period changed, so the work orders were removed. Generate and schedule them again.")
            plan["work_orders"], plan["schedule_run"] = [], None
    for k in ("forecast_id", "capacity_plan_id"):
        if k in body and body[k] is not None:
            plan[k] = body[k] or None
    if body.get("wizard_state") is not None:
        if len(json.dumps(body["wizard_state"], default=str)) > 20_000:
            raise _field_error(field="wizard_state", message="The wizard state is too large to keep.")
        plan["wizard_state"] = body["wizard_state"]
    if body.get("wizard_step") is not None:
        assert_step_reachable(plan, int(body["wizard_step"]))
        plan["wizard_step"] = int(body["wizard_step"])
    return notes


# --- step 1: demand ---------------------------------------------------------------------------------------------------


def _order_lines(view: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {ln["order_id"]: (h, ln) for h in view["orders"] for ln in h["lines"]}


def candidates(plan: Doc, view: dict[str, Any]) -> list[dict[str, Any]]:
    """Open order lines with quantity not yet in any plan (ordered - planned > 0); those due in the period first."""
    inside = {d["order_id"] for d in plan["demands"]}
    out = []
    for h in view["orders"]:
        if h["status"] == "Cancelled":
            continue
        for ln in h["lines"]:
            left = ln["ordered_qty"] - ln["planned_qty"]
            if left <= 0 or ln["order_id"] in inside or ln["status"] in ("Cancelled", *orders.DONE_STATUSES):
                continue
            out.append(
                {
                    "order_id": ln["order_id"],
                    "order_number": h["order_number"],
                    "customer_id": h["customer_id"],
                    "customer_name": h["customer_name"],
                    "sku": ln["sku"],
                    "product_name": ln["product_name"],
                    "ordered_qty": ln["ordered_qty"],
                    "planned_qty": ln["planned_qty"],
                    "outstanding_qty": left,
                    "requested_delivery_date": ln["requested_delivery_date"],
                    "in_period": plan["period_start"] <= ln["requested_delivery_date"] <= plan["period_end"],
                    "at_risk": ln["at_risk"],
                }
            )
    return sorted(out, key=lambda c: (not c["in_period"], c["requested_delivery_date"], c["order_number"], c["sku"]))


def _line(plan: Doc, line_id: str) -> Doc:
    ln: Doc | None = next((x for x in plan["lines"] if x["line_id"] == line_id), None)
    if ln is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown plan line {line_id}", {"line_id": line_id})
    return ln


def _line_for_sku(plan: Doc, sku: str, delivery: str | None) -> Doc:
    ln = next((x for x in plan["lines"] if x["sku"] == sku), None)
    if ln is None:
        ln = {
            "line_id": f"ppl-{uuid.uuid4().hex[:10]}",
            "sku": sku,
            "demand_qty": 0,
            "forecast_qty": 0,
            "forecast_line_id": None,
            "planned_qty": 0,
            "planned_overridden": False,
            "required_delivery_date": delivery,
            "priority": len(plan["lines"]) + 1,
            "capacity_status": "Within Plan",
            "capacity": None,
            "status": "Draft",
        }
        plan["lines"].append(ln)
    return ln


def _set_demand(ln: Doc, demand_qty: int, forecast_qty: int) -> None:
    """Planned follows demand until the planner sets it in step 2; it never stays above the demand it came from."""
    ln["demand_qty"], ln["forecast_qty"] = demand_qty, forecast_qty
    total = demand_qty + forecast_qty
    if not ln.get("planned_overridden"):
        ln["planned_qty"] = total
    ln["planned_qty"] = min(ln["planned_qty"], total)


def _drop_work_orders(plan: Doc, line_id: str | None = None) -> bool:
    keep = [w for w in plan["work_orders"] if line_id is not None and w["line_id"] != line_id]
    dropped = len(keep) != len(plan["work_orders"])
    plan["work_orders"] = keep
    if dropped:
        plan["schedule_run"] = None
    return dropped


def add_demand(plan: Doc, view: dict[str, Any], order_id: str, qty: int | None) -> Doc:
    assert_editable(plan)
    found = _order_lines(view).get(order_id)
    if found is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown customer order line {order_id}", {"order_id": order_id})
    h, ol = found
    if h["status"] == "Cancelled" or ol["status"] == "Cancelled":
        raise FVError(DEMAND_NOT_PLANNABLE, f"Order {h['order_number']} is cancelled; a cancelled order cannot enter a production plan.")
    dup = next((d for d in plan["demands"] if d["order_id"] == order_id), None)
    if dup:
        raise FVError(DEMAND_NOT_PLANNABLE, f"{h['order_number']} {ol['sku']} is already in this plan.", {"demand_id": dup["demand_id"]})
    left = ol["ordered_qty"] - ol["planned_qty"]
    q = left if qty is None else qty
    if q <= 0:
        if left <= 0:
            raise _field_error(
                field="demand_qty",
                message=f"Nothing is left to plan on {h['order_number']} {ol['sku']}: {ol['planned_qty']:,} of {ol['ordered_qty']:,} are already planned.",
                headline="This order line is already fully planned.",
            )
        raise _field_error(field="demand_qty", message="Demand quantity must be more than zero.")
    if q > left:
        raise _field_error(
            field="demand_qty",
            message=f"{q:,} is more than the {left:,} still open on this order line (ordered {ol['ordered_qty']:,}, planned {ol['planned_qty']:,}).",
            headline="Demand exceeds what is open on the order line.",
        )
    delivery = ol["requested_delivery_date"]
    ln = _line_for_sku(plan, ol["sku"], delivery)
    demand = {
        "demand_id": f"ppd-{uuid.uuid4().hex[:10]}",
        "line_id": ln["line_id"],
        "order_id": order_id,
        "order_number": h["order_number"],
        "customer_id": h["customer_id"],
        "customer_name": h["customer_name"],
        "sku": ol["sku"],
        "demand_qty": q,
        "requested_delivery_date": delivery,
    }
    plan["demands"].append(demand)
    # a plan line is only as late as its most urgent customer
    ln["required_delivery_date"] = min(x for x in (ln["required_delivery_date"], delivery) if x)
    _set_demand(ln, ln["demand_qty"] + q, ln["forecast_qty"])
    _drop_work_orders(plan, ln["line_id"])
    return demand


def _settle_line(plan: Doc, ln: Doc) -> None:
    rows = [d for d in plan["demands"] if d["line_id"] == ln["line_id"]]
    if not rows and not ln["forecast_qty"]:
        plan["lines"] = [x for x in plan["lines"] if x["line_id"] != ln["line_id"]]  # no demand behind it, no decision left
        for k, x in enumerate(sorted(plan["lines"], key=lambda x: x["priority"])):
            x["priority"] = k + 1
        return
    _set_demand(ln, sum(int(d["demand_qty"]) for d in rows), ln["forecast_qty"])
    dates = [d["requested_delivery_date"] for d in rows if d["requested_delivery_date"]]
    ln["required_delivery_date"] = min(dates) if dates else plan["period_end"]


def remove_demand(plan: Doc, demand_id: str) -> None:
    assert_editable(plan)
    d = next((x for x in plan["demands"] if x["demand_id"] == demand_id), None)
    if d is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown plan demand {demand_id}", {"demand_id": demand_id})
    plan["demands"] = [x for x in plan["demands"] if x["demand_id"] != demand_id]
    _drop_work_orders(plan, d["line_id"])
    _settle_line(plan, _line(plan, d["line_id"]))


def forecast_open_qty(sku: str, start: date, end: date, demand_change_pct: dict[str, float] | None = None) -> dict[str, Any]:
    """Forecast not yet covered by firm orders in the period: each forecast week's uncovered qty, by working days inside the period."""
    vs = forecast.forecast_vs_orders(sku, demand_change_pct or None)
    qty = 0.0
    weeks = []
    for w in vs["weeks"]:
        ws = to_date(w["week_start"])
        we = ws + timedelta(days=7)
        lo, hi = max(ws, start), min(we, end + timedelta(days=1))
        if hi <= lo:
            continue
        share = working_days_between(lo, hi) / max(1, working_days_between(ws, we))
        part = w["uncovered_qty"] * share
        qty += part
        weeks.append({"week_start": w["week_start"], "uncovered_qty": w["uncovered_qty"], "share": round(share, 3), "qty": round(part)})
    return {"sku": sku, "qty": round(qty), "weeks": weeks, "engine": vs["engine"], "source": vs["source"]}


def set_forecast_demand(plan: Doc, sku: str, qty: int | None, demand_change_pct: dict[str, float] | None = None) -> Doc:
    """Forecast not yet ordered as extra demand on the SKU's line (qty 0 removes it). Numbers come from the forecast engine."""
    assert_editable(plan)
    if sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    fc = forecast_open_qty(sku, to_date(plan["period_start"]), to_date(plan["period_end"]), demand_change_pct)
    q = fc["qty"] if qty is None else qty
    if q < 0:
        raise _field_error(field="forecast_qty", message="Forecast quantity cannot be negative.")
    ln = _line_for_sku(plan, sku, plan["period_end"])
    ln["forecast_line_id"] = f"{plan.get('forecast_id') or 'live'}:{sku}:{plan['period_start']}" if q else None
    _set_demand(ln, ln["demand_qty"], q)
    _drop_work_orders(plan, ln["line_id"])
    _settle_line(plan, ln)
    return {**fc, "applied_qty": q}


# --- step 2: planned quantity and capacity ----------------------------------------------------------------------------


def update_line(plan: Doc, line_id: str, planned_qty: int | None, priority: int | None) -> bool:
    """Step 2's decision: how much to make, at what priority. Returns whether the line's work orders were dropped."""
    assert_editable(plan)
    ln = _line(plan, line_id)
    dropped = False
    if planned_qty is not None:
        if planned_qty < 0:
            raise _field_error(field="planned_qty", message="Planned quantity cannot be negative.")
        if planned_qty != ln["planned_qty"]:
            ln["planned_qty"], ln["planned_overridden"] = planned_qty, planned_qty != ln["demand_qty"] + ln["forecast_qty"]
            dropped = _drop_work_orders(plan, line_id)
    if priority is not None:
        if priority < 1:
            raise _field_error(field="priority", message="Priority starts at 1 (most urgent).")
        others = sorted((x for x in plan["lines"] if x["line_id"] != line_id), key=lambda x: x["priority"])
        others.insert(min(priority, len(others) + 1) - 1, ln)
        for k, x in enumerate(others):
            x["priority"] = k + 1
    return dropped


def cycle_s(sku: str, mid: str, rates: dict[str, dict[str, Any]]) -> float | None:
    r = rates.get(mid)
    if not r or not r.get("yield"):
        return None
    return float(plant.PRODUCT_BY_SKU[sku].ideal_cycle_s * r["cycle_factor"] / r["yield"])


def _production_days(plan: Doc) -> list[date]:
    d, end = max(to_date(plan["period_start"]), TODAY), to_date(plan["period_end"])
    out = []
    while d <= end:
        if plant.is_working_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def committed_minutes(plan: Doc, view: dict[str, Any], rates: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Molding minutes already promised in the period: open order lines planned outside this plan and due by its end.

    They run on the machine holding the SKU's mold; what is in stock is already made.
    """
    mine: dict[str, int] = {}
    for d in plan["demands"]:
        mine[d["order_id"]] = mine.get(d["order_id"], 0) + int(d["demand_qty"])
    molds = planning.current_molds()
    out = dict.fromkeys(plant.IMM_IDS, 0.0)
    for h in view["orders"]:
        for ln in h["lines"]:
            if ln["status"] in ("Cancelled", *orders.DONE_STATUSES) or ln["requested_delivery_date"] > plan["period_end"]:
                continue
            todo = ln["planned_qty"] - mine.get(ln["order_id"], 0) - ln["produced_qty"]
            mid = molds.get(ln["sku"])
            cyc = cycle_s(ln["sku"], mid, rates) if mid else None
            if todo > 0 and mid and cyc:
                out[mid] += todo * cyc / 60
    return out


def _status(load: float, total: float, utilization_pct: float) -> str:
    if load > total:
        return "Capacity Up Required"
    return "Additional Demand" if load > total * utilization_pct / 100 else "Within Plan"


def assess(plan: Doc, view: dict[str, Any], rates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Capacity of every plan line, in priority order: a line can use the time its compatible machines have left.

    A SKU has one mold, so it runs on one machine at a time: Total = the most pieces one compatible machine can make
    in its free minutes at the actual cycle time (a machine other than the mold's own pays a changeover).
    Planning = Total x utilization; Buffer = Total - Planning; Gap = max(planned - Total, 0). Within Plan up to
    Planning, Additional Demand up to Total, Capacity Up Required above it. The line then takes that machine's
    time, so lines after it see what is left.
    """
    days = _production_days(plan)
    per_machine = len(days) * MINUTES_PER_WORKDAY * AVAILABLE_FACTOR
    committed = committed_minutes(plan, view, rates)
    free = {mid: max(0.0, per_machine - committed[mid]) for mid in plant.IMM_IDS}
    molds = planning.current_molds()
    util = float(plan["planning_utilization_pct"])
    result: dict[str, Any] = {}
    for ln in sorted(plan["lines"], key=lambda x: x["priority"]):
        sku, need = ln["sku"], int(ln["planned_qty"])
        home = molds.get(sku)
        contrib: list[dict[str, Any]] = []
        uncomputed: list[dict[str, str]] = []
        for mid in sorted(plant.MOLD_COMPAT[sku], key=lambda m: (m != home, -free[m])):
            cyc = cycle_s(sku, mid, rates)
            if cyc is None:
                uncomputed.append({"machine_id": mid, "reason": "No production in the last 7 days, so there is no actual cycle time. Not counted as zero."})
                continue
            usable = free[mid] - (0 if mid == home else plant.CHANGEOVER_MINUTES)
            contrib.append(
                {
                    "machine_id": mid,
                    "free_minutes": round(max(0.0, usable)),
                    "cycle_s": round(cyc, 1),
                    "capacity_qty": int(max(0.0, usable) * 60 // cyc),
                    "home": mid == home,
                    "allocated_qty": 0,
                }
            )
        best = max(contrib, key=lambda c: (c["capacity_qty"], c["home"]), default=None)
        total = best["capacity_qty"] if best else 0
        planning_cap = int(total * util / 100)
        if best and need:
            take = min(need, total)
            best["allocated_qty"] = take
            free[best["machine_id"]] -= take * best["cycle_s"] / 60 + (0 if best["home"] else plant.CHANGEOVER_MINUTES)
        result[ln["line_id"]] = {
            "sku": sku,
            "planned_qty": need,
            "total_capacity": total,
            "planning_capacity": planning_cap,
            "buffer": total - planning_cap,
            "gap": max(need - total, 0),
            "utilization_pct": pct(need / total) if total else None,
            "status": _status(need, total, util) if need else "Within Plan",
            "contributions": contrib,
            "uncomputed_machines": uncomputed,
        }
    return {
        "lines": result,
        "production_days": len(days),
        "minutes_per_machine": round(per_machine),
        "committed_minutes": {k: round(v) for k, v in committed.items()},
        "planning_utilization_pct": util,
        "rule": "One mold, one machine at a time: Total = best compatible machine's free minutes / actual cycle; Planning = Total x utilization; lines take capacity in priority order",
        "source": "fv.fact_production (cycle and yield, last 7 days), fv.dim_mold, fv.product_mold_compatibility, open customer orders",
    }


def apply_assessment(plan: Doc, view: dict[str, Any], rates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a = assess(plan, view, rates)
    for ln in plan["lines"]:
        cap = a["lines"].get(ln["line_id"])
        ln["capacity"], ln["capacity_status"] = cap, cap["status"] if cap else "Within Plan"
    return a


# --- step 3: work orders ----------------------------------------------------------------------------------------------


def _wo_number(plan: Doc, proc: str, sku: str, taken: set[str]) -> str:
    base = f"WO-{to_date(plan['period_start']):%y%m%d}-{proc}-{sku}"
    n, num = 2, base
    while num in taken:
        num, n = f"{base}-{n}", n + 1
    return num


def generate_work_orders(plan: Doc, taken: set[str]) -> dict[str, Any]:
    """One work order per routing process per line (molding, painting, assembly), each for the line's planned qty.

    Regenerating creates no duplicates and reports what already existed. Every routing is checked before anything is
    written, so a bad one leaves nothing behind.
    """
    assert_editable(plan)
    lines = sorted(plan["lines"], key=lambda x: x["priority"])
    if not lines:
        raise FVError(STEP_NOT_READY, "The plan has no lines yet. Add demand in step 1 first.")
    bad = [ln["sku"] for ln in lines if ln["planned_qty"] > 0 and not plant.MOLD_COMPAT.get(ln["sku"])]
    if bad:
        raise FVError(
            INVALID_REQUEST,
            "No work order was created: these products have no molding machine in their routing.",
            {"fields": [{"field": f"product:{s}", "message": "No compatible machine."} for s in bad]},
        )
    created, existing, skipped = [], [], []
    for ln in lines:
        if ln["planned_qty"] <= 0:
            skipped.append(ln["sku"])
            continue
        prev = None
        for seq, (proc, name) in enumerate(PROCESSES, start=1):
            wo = next((w for w in plan["work_orders"] if w["line_id"] == ln["line_id"] and w["process"] == proc), None)
            if wo is None:
                num = _wo_number(plan, proc, ln["sku"], taken)
                taken.add(num)
                wo = {
                    "wo_id": num,
                    "line_id": ln["line_id"],
                    "sku": ln["sku"],
                    "process": proc,
                    "process_name": name,
                    "sequence": seq,
                    "predecessor_wo_id": prev,
                    "planned_qty": ln["planned_qty"],
                    "priority": ln["priority"],
                    "required_delivery_date": ln["required_delivery_date"],
                    "status": "Draft",
                    "planned_start": None,
                    "planned_end": None,
                    "shift": None,
                    "machine_id": None,
                    "mold_id": None,
                    "runs": [],
                    "short_qty": 0,
                    "late": [],
                }
                plan["work_orders"].append(wo)
                created.append(num)
            else:
                existing.append(wo["wo_id"])
            prev = wo["wo_id"]
    return {"created": created, "existing": existing, "skipped_skus": skipped}


# --- step 4: scheduling (CP-SAT) --------------------------------------------------------------------------------------


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, DAY_START_HOUR, tzinfo=plant.WIB)


def _production_day(t: datetime) -> date:
    return (t - timedelta(hours=DAY_START_HOUR)).date()


def _add_work(t: datetime, minutes: float) -> datetime:
    """Advance t by minutes of work on the three-shift clock, skipping non-working days."""
    while True:
        d = _production_day(t)
        if not plant.is_working_day(d):
            t = _day_start(d + timedelta(days=1))
            continue
        room = (_day_start(d + timedelta(days=1)) - t).total_seconds() / 60
        if minutes <= room:
            return t + timedelta(minutes=minutes)
        minutes -= room
        t = _day_start(d + timedelta(days=1))


def schedule(plan: Doc, view: dict[str, Any], rates: dict[str, dict[str, Any]], time_limit_s: float = 10.0) -> dict[str, Any]:
    """Day-by-machine molding schedule for the plan's work orders, then painting and assembly behind it.

    CP-SAT: qty per (work order, machine, day) on compatible machines only; one mold is on one machine a day;
    a mold arriving on a machine costs a 90-minute changeover; each machine-day keeps the minutes already promised
    to orders outside the plan. Minimises unmet qty (by priority), lateness, changeovers and mold moves.
    """
    assert_editable(plan)
    t0 = time.time()
    mold_wos = sorted((w for w in plan["work_orders"] if w["process"] == "MLD"), key=lambda w: w["priority"])
    if not mold_wos:
        raise FVError(STEP_NOT_READY, "There are no work orders to schedule. Generate them in step 3 first.")
    days = _production_days(plan)
    if not days:
        raise FVError(STEP_NOT_READY, "No working day is left in the plan period.")
    committed = committed_minutes(plan, view, rates)
    molds = planning.current_molds()
    home_sku = {mid: sku for sku, mid in molds.items()}
    per_day = MINUTES_PER_WORKDAY * AVAILABLE_FACTOR
    busy = _front_loaded(committed, len(days), per_day)
    scale = 10  # deciseconds
    n = len(mold_wos)

    m = cp_model.CpModel()
    x: dict[tuple[int, str, int], cp_model.IntVar] = {}
    on: dict[tuple[int, str, int], cp_model.IntVar] = {}
    starts: dict[tuple[int, str, int], cp_model.IntVar] = {}
    moves: list[cp_model.IntVar] = []
    cyc: dict[tuple[int, str], int] = {}
    for i, w in enumerate(mold_wos):
        for mid in plant.MOLD_COMPAT[w["sku"]]:
            c = cycle_s(w["sku"], mid, rates)
            if c is None:
                continue
            cyc[i, mid] = round(c * scale)
            home = molds.get(w["sku"])
            for k in range(len(days)):
                if mid != home and (busy[mid][k] or (home and busy[home][k])):
                    continue  # a mold leaves its machine, or takes another's, only once the promised orders there are done
                x[i, mid, k] = m.new_int_var(0, w["planned_qty"], f"x{i}_{mid}_{k}")
                on[i, mid, k] = m.new_bool_var(f"on{i}_{mid}_{k}")
                m.add(x[i, mid, k] == 0).only_enforce_if(on[i, mid, k].negated())
                m.add(x[i, mid, k] >= 1).only_enforce_if(on[i, mid, k])
                s = m.new_bool_var(f"s{i}_{mid}_{k}")
                before = on.get((i, mid, k - 1), 0) if k else (1 if home_sku.get(mid) == w["sku"] else 0)
                m.add(s >= on[i, mid, k] - before)
                starts[i, mid, k] = s
                if mid != molds.get(w["sku"]):
                    moves.append(on[i, mid, k])
        for k in range(len(days)):
            m.add(sum(on[i, mid, k] for mid in plant.MOLD_COMPAT[w["sku"]] if (i, mid, k) in on) <= 1)
    for mid in plant.IMM_IDS:
        for k in range(len(days)):
            cap = int(max(0.0, per_day - busy[mid][k]) * 60 * scale)
            here = [i for i in range(n) if (i, mid, k) in x]
            if not here:
                continue
            m.add(sum(x[i, mid, k] * cyc[i, mid] for i in here) + sum(starts[i, mid, k] for i in here) * plant.CHANGEOVER_MINUTES * 60 * scale <= cap)
    unmet, late = [], []
    for i, w in enumerate(mold_wos):
        made = [x[i, mid, k] for (j, mid, k) in x if j == i]
        u = m.new_int_var(0, w["planned_qty"], f"u{i}")
        m.add(sum(made) + u == w["planned_qty"])
        # a missing piece costs its machine time, more for a higher priority, so priority wins over a shorter cycle
        weight = round(plant.PRODUCT_BY_SKU[w["sku"]].ideal_cycle_s) * 10 * (n - i + 1)
        unmet.append(weight * u)
        # every delivery date needs its cumulative demand molded MOLD_LEAD_DAYS ahead; what is missing is backlog
        for due, need in _due_profile(plan, w):
            ready = [x[i, mid, k] for (j, mid, k) in x if j == i and days[k] <= due - timedelta(days=MOLD_LEAD_DAYS)]
            b = m.new_int_var(0, need, f"b{i}_{due:%m%d}")
            m.add(sum(ready) + b >= need)
            late.append(20 * b)
    early = [var * k for (_i, _mid, k), var in x.items()]
    m.minimize(sum(unmet) + sum(late) + 300 * sum(starts.values()) + 200 * sum(moves) + sum(early))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise FVError(NO_FEASIBLE_PLAN, detail={"solver_status": solver.status_name(status)})

    # Turn quantities into a timeline: each machine-day runs the promised orders first, then plan work by priority.
    clock = {(mid, k): _day_start(days[k]) + timedelta(minutes=busy[mid][k] / AVAILABLE_FACTOR) for mid in plant.IMM_IDS for k in range(len(days))}
    changeovers = 0
    for i, w in enumerate(mold_wos):
        runs: list[dict[str, Any]] = []
        for k in range(len(days)):
            for mid in plant.MOLD_COMPAT[w["sku"]]:
                if (i, mid, k) not in x or not solver.value(x[i, mid, k]):
                    continue
                q = solver.value(x[i, mid, k])
                prev_on = solver.value(on[i, mid, k - 1]) if k and (i, mid, k - 1) in on else (not k and home_sku.get(mid) == w["sku"])
                co = 0 if prev_on else plant.CHANGEOVER_MINUTES
                changeovers += 1 if co else 0
                start = clock[mid, k]
                end = start + timedelta(minutes=(q * cyc[i, mid] / scale / 60 + co) / AVAILABLE_FACTOR)
                clock[mid, k] = end
                runs.append({"date": days[k].isoformat(), "machine_id": mid, "qty": q, "start": _iso(start), "end": _iso(end), "changeover": bool(co)})
        made = sum(r["qty"] for r in runs)
        main = max(runs, key=lambda r: r["qty"])["machine_id"] if runs else molds.get(w["sku"])
        w.update(
            runs=runs,
            short_qty=w["planned_qty"] - made,
            machine_id=main,
            mold_id=plant.MOLD_ID[w["sku"]],
            planned_start=runs[0]["start"] if runs else None,
            planned_end=max(r["end"] for r in runs) if runs else None,
            shift=plant.shift_of(datetime.fromisoformat(runs[0]["start"])) if runs else None,
            status="Scheduled" if runs else "Draft",
        )
    _schedule_downstream(plan)
    late_wos = []
    for w in mold_wos:
        w["late"] = _late(w, plan)
        if w["late"]:
            late_wos.append({"wo_id": w["wo_id"], "sku": w["sku"], "dates": w["late"]})
    total = sum(w["planned_qty"] for w in mold_wos)
    short = sum(w["short_qty"] for w in mold_wos)
    run = {
        "engine": "OR-Tools CP-SAT",
        "status": solver.status_name(status),
        "solve_seconds": round(time.time() - t0, 2),
        "production_days": len(days),
        "fill_rate_pct": pct(1 - short / total) if total else 100.0,
        "short_qty": {w["sku"]: w["short_qty"] for w in mold_wos if w["short_qty"]},
        "changeovers": changeovers,
        "mold_moves": sorted({f"{w['sku']} -> {r['machine_id']}" for w in mold_wos for r in w["runs"] if r["machine_id"] != molds.get(w["sku"])}),
        "late_work_orders": late_wos,
        "committed_minutes": {k: round(v) for k, v in committed.items()},
        "assumptions": {
            "minutes_per_workday": MINUTES_PER_WORKDAY,
            "available_factor": AVAILABLE_FACTOR,
            "changeover_minutes": plant.CHANGEOVER_MINUTES,
            "paint_takt_s": PAINT_TAKT_S,
            "assembly_takt_s": ASSEMBLY_TAKT_S,
        },
        "ran_at": _now(),
    }
    plan["schedule_run"], plan["schedule_stale"] = run, False
    return run


def _front_loaded(committed: dict[str, float], n_days: int, per_day: float) -> dict[str, list[float]]:
    """Promised minutes per machine-day: orders already promised run first, filling the earliest days."""
    out = {}
    for mid, total in committed.items():
        left, row = total, []
        for _ in range(n_days):
            take = min(left, per_day)
            row.append(take)
            left -= take
        out[mid] = row
    return out


def _due_profile(plan: Doc, wo: Doc) -> list[tuple[date, int]]:
    """Cumulative demand of a work order's line by delivery date (forecast demand is due at period end), capped at planned."""
    by_date: dict[date, int] = {}
    for d in plan["demands"]:
        if d["line_id"] == wo["line_id"]:
            day = to_date(d["requested_delivery_date"] or plan["period_end"])
            by_date[day] = by_date.get(day, 0) + int(d["demand_qty"])
    ln = _line(plan, wo["line_id"])
    if ln["forecast_qty"]:
        end = to_date(plan["period_end"])
        by_date[end] = by_date.get(end, 0) + int(ln["forecast_qty"])
    out, cum = [], 0
    for day in sorted(by_date):
        cum = min(cum + by_date[day], int(wo["planned_qty"]))
        out.append((day, cum))
    return out


def _late(wo: Doc, plan: Doc) -> list[dict[str, Any]]:
    """Delivery dates whose cumulative demand is not molded MOLD_LEAD_DAYS ahead, with the qty missing."""
    out = []
    for due, need in _due_profile(plan, wo):
        made = sum(r["qty"] for r in wo["runs"] if to_date(r["date"]) <= due - timedelta(days=MOLD_LEAD_DAYS))
        if made < need:
            out.append({"delivery_date": due.isoformat(), "short_qty": need - made})
    return out


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="minutes")


def _schedule_downstream(plan: Doc) -> None:
    """Painting one shift behind molding on the shared paint line; assembly behind painting on the least busy cell."""
    by_line = {w["line_id"]: w for w in plan["work_orders"] if w["process"] == "MLD"}
    paint_free: datetime | None = None
    cell_free: dict[str, datetime | None] = dict.fromkeys(ASSEMBLY_CELLS)
    for mw in sorted(by_line.values(), key=lambda w: (w["planned_start"] or "9", w["priority"])):
        pw = next((w for w in plan["work_orders"] if w["line_id"] == mw["line_id"] and w["process"] == "PNT"), None)
        aw = next((w for w in plan["work_orders"] if w["line_id"] == mw["line_id"] and w["process"] == "ASM"), None)
        if not mw["planned_start"]:
            for w in (pw, aw):
                if w:
                    w.update(status="Draft", planned_start=None, planned_end=None, shift=None, machine_id=None)
            continue
        ms, me = datetime.fromisoformat(mw["planned_start"]), datetime.fromisoformat(mw["planned_end"])
        qty = mw["planned_qty"] - mw["short_qty"]
        if pw:
            ps = _add_work(ms, PAINT_LAG_MIN)
            if paint_free and paint_free > ps:
                ps = paint_free
            pe = max(_add_work(ps, qty * PAINT_TAKT_S / 60), _add_work(me, PAINT_LAG_MIN / 2))
            paint_free = _add_work(ps, qty * PAINT_TAKT_S / 60)
            pw.update(
                status="Scheduled",
                planned_start=_iso(ps),
                planned_end=_iso(pe),
                shift=plant.shift_of(ps),
                machine_id=PAINT_LINE,
                short_qty=mw["short_qty"],
            )
            if aw:
                cell = min(ASSEMBLY_CELLS, key=lambda c: cell_free[c] or datetime.min.replace(tzinfo=plant.WIB))
                start = _add_work(ps, ASSEMBLY_LAG_MIN)
                free = cell_free[cell]
                if free and free > start:
                    start = free
                end = max(_add_work(start, qty * ASSEMBLY_TAKT_S / 60), _add_work(pe, ASSEMBLY_LAG_MIN / 2))
                cell_free[cell] = _add_work(start, qty * ASSEMBLY_TAKT_S / 60)
                aw.update(
                    status="Scheduled",
                    planned_start=_iso(start),
                    planned_end=_iso(end),
                    shift=plant.shift_of(start),
                    machine_id=cell,
                    short_qty=mw["short_qty"],
                )


# --- step 5: resources ------------------------------------------------------------------------------------------------


def resource_options(wo: Doc) -> dict[str, list[str]]:
    if wo["process"] == "MLD":
        return {"machines": list(plant.MOLD_COMPAT[wo["sku"]]), "molds": [plant.MOLD_ID[wo["sku"]]]}
    if wo["process"] == "PNT":
        return {"machines": [PAINT_LINE], "molds": []}
    return {"machines": list(ASSEMBLY_CELLS), "molds": []}


def resourced(wo: Doc) -> bool:
    return bool(wo["machine_id"]) and (wo["process"] != "MLD" or bool(wo["mold_id"]))


def set_resource(plan: Doc, wo_id: str, machine_id: str | None, mold_id: str | None) -> list[str]:
    """Machine and mold of one work order; only a compatible pair is accepted."""
    assert_editable(plan)
    wo = next((w for w in plan["work_orders"] if w["wo_id"] == wo_id), None)
    if wo is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown work order {wo_id}", {"wo_id": wo_id})
    if wo["status"] == "Confirmed":
        raise FVError(PLAN_LOCKED, f"Work order {wo_id} is confirmed; its resources can no longer change.")
    opts = resource_options(wo)
    errors = []
    if machine_id is not None and machine_id not in opts["machines"]:
        errors.append(
            {"field": "machine_id", "message": f"{machine_id} cannot run {wo['process_name'].lower()} for {wo['sku']}. Choose {', '.join(opts['machines'])}."}
        )
    if mold_id is not None and mold_id not in opts["molds"]:
        errors.append({"field": "mold_id", "message": f"{mold_id} does not fit {wo['sku']}." if opts["molds"] else "This process uses no mold."})
    if errors:
        raise FVError(INVALID_REQUEST, "The resource does not fit this work order.", {"fields": errors})
    notes = []
    if machine_id is not None and machine_id != wo["machine_id"]:
        wo["machine_id"] = machine_id
        if wo["process"] == "MLD" and wo["runs"]:
            for r in wo["runs"]:
                r["machine_id"] = machine_id
            plan["schedule_stale"] = True
            notes.append(f"{wo_id} moved to {machine_id}. Run the scheduler again to check capacity there.")
    if mold_id is not None:
        wo["mold_id"] = mold_id
    return notes


def confirm_work_orders(plan: Doc) -> int:
    assert_editable(plan)
    wos = plan["work_orders"]
    if not wos:
        raise FVError(STEP_NOT_READY, "There are no work orders yet. Generate them in step 3.")
    open_ = [w["wo_id"] for w in wos if w["status"] == "Draft" or not resourced(w)]
    if open_:
        raise FVError(
            STEP_NOT_READY, f"{len(open_)} work order(s) are not scheduled with a machine and mold yet: {', '.join(open_[:4])}.", {"work_orders": open_}
        )
    n = 0
    for w in wos:
        if w["status"] != "Confirmed":
            w["status"], n = "Confirmed", n + 1
    return n


# --- readiness, confirm, cancel ---------------------------------------------------------------------------------------


def readiness(plan: Doc) -> dict[str, Any]:
    wos = plan["work_orders"]
    r = {
        "current_step": plan["wizard_step"],
        "demand_count": len(plan["demands"]) + sum(1 for ln in plan["lines"] if ln["forecast_qty"]),
        "line_count": len(plan["lines"]),
        "lines_with_planned_qty": sum(1 for ln in plan["lines"] if ln["planned_qty"] > 0),
        "work_order_count": len(wos),
        "scheduled_work_orders": sum(1 for w in wos if w["status"] != "Draft"),
        "resourced_work_orders": sum(1 for w in wos if resourced(w)),
        "confirmed_work_orders": sum(1 for w in wos if w["status"] == "Confirmed"),
        "capacity_up_required_lines": [ln["sku"] for ln in plan["lines"] if ln["capacity_status"] == "Capacity Up Required"],
    }
    missing = r["work_order_count"] - min(r["resourced_work_orders"], r["scheduled_work_orders"])
    blocked = {
        2: None if r["demand_count"] else "Add at least one demand in step 1 before setting planned quantities.",
        3: None if r["lines_with_planned_qty"] else "Give at least one plan line a planned quantity in step 2 before generating work orders.",
        4: None if r["work_order_count"] else "Generate work orders in step 3 before scheduling.",
        5: None
        if r["scheduled_work_orders"]
        else ("Generate work orders in step 3 first." if not r["work_order_count"] else "Schedule the work orders in step 4 before assigning resources."),
        6: None
        if r["work_order_count"] and not missing
        else ("Generate work orders in step 3 first." if not r["work_order_count"] else f"{missing} work order(s) have no schedule, machine or mold yet."),
    }
    steps, furthest = [{"step": 1, "label": STEP_LABELS[1], "reachable": True, "blocked_by": None}], 1
    for s in range(2, 7):
        ok = blocked[s] is None and steps[-1]["reachable"]
        steps.append({"step": s, "label": STEP_LABELS[s], "reachable": ok, "blocked_by": None if ok else blocked[s] or steps[-1]["blocked_by"]})
        furthest = s if ok else furthest
    final = plan["status"] not in EDITABLE
    return {**r, "steps": steps, "furthest_step": 6 if final else furthest, "resume_step": 6 if final else min(plan["wizard_step"], furthest)}


def assert_step_reachable(plan: Doc, step: int) -> None:
    if step < 1 or step > 6:
        raise _field_error(field="wizard_step", message="The wizard has steps 1 to 6.")
    s = readiness(plan)["steps"][step - 1]
    if not s["reachable"]:
        raise FVError(STEP_NOT_READY, s["blocked_by"], {"step": step})


def refresh_status(plan: Doc) -> None:
    if plan["status"] not in EDITABLE:
        return
    if not plan["lines"]:
        plan["status"] = "Draft"
    else:
        r = readiness(plan)
        plan["status"] = "Ready" if r["steps"][5]["reachable"] else "Planning"
    plan["wizard_step"] = min(plan["wizard_step"], readiness(plan)["furthest_step"])


def confirm(plan: Doc, user: str) -> None:
    """The plan turns from an intention into a commitment; the orders it serves show as Planned."""
    if plan["status"] in ("Confirmed", "Cancelled"):
        raise FVError(PLAN_LOCKED, f"Production plan {plan['plan_number']} is already {plan['status'].lower()}.")
    if not plan["lines"]:
        raise FVError(STEP_NOT_READY, "A plan without lines cannot be confirmed.")
    blocked = [ln["sku"] for ln in plan["lines"] if ln["capacity_status"] == "Capacity Up Required"]
    if blocked:
        raise FVError(
            STEP_NOT_READY,
            f"{len(blocked)} plan line(s) need more capacity than the plant has: {', '.join(blocked)}. Lower the planned quantity or add capacity before confirming.",
            {"skus": blocked},
        )
    if not plan["work_orders"]:
        raise FVError(STEP_NOT_READY, "No work orders have been generated for this plan yet. Generate them in step 3.")
    unconfirmed = [w["wo_id"] for w in plan["work_orders"] if w["status"] != "Confirmed"]
    if unconfirmed:
        raise FVError(
            STEP_NOT_READY, f"{len(unconfirmed)} work order(s) are not confirmed yet. Confirm every work order before the plan.", {"work_orders": unconfirmed}
        )
    plan.update(status="Confirmed", confirmed_by=user, confirmed_at=_now(), wizard_step=6)
    for ln in plan["lines"]:
        ln["status"] = "Released"


def wo_status(plan: Doc, wo: Doc) -> str:
    """Once the plan is confirmed, a work order that has started by the plant's today is In Production."""
    if plan["status"] != "Confirmed" or wo["status"] != "Confirmed" or not wo["planned_start"]:
        return str(wo["status"])
    return "In Production" if _production_day(datetime.fromisoformat(wo["planned_start"])) <= TODAY else "Confirmed"


def display_status(plan: Doc) -> str:
    if plan["status"] != "Confirmed":
        return str(plan["status"])
    if to_date(plan["period_end"]) < TODAY:
        return "Completed"
    if to_date(plan["period_start"]) <= TODAY or any(wo_status(plan, w) == "In Production" for w in plan["work_orders"]):
        return "In Execution"
    return "Confirmed"


def cancel(plan: Doc, reason: str, user: str) -> None:
    """Cancelling gives the demand back to the order lines; a plan with work orders already running cannot be cancelled."""
    reason = reason.strip()
    if len(reason) < 3:
        raise _field_error(field="reason", message="Give a reason of at least 3 characters.", headline="A cancellation reason is required.")
    if plan["status"] == "Cancelled":
        raise FVError(PLAN_LOCKED, f"Production plan {plan['plan_number']} is already cancelled.")
    running = [w["wo_id"] for w in plan["work_orders"] if wo_status(plan, w) in ("In Production", "Completed")]
    if running:
        raise FVError(
            PLAN_LOCKED,
            f"Production plan {plan['plan_number']} cannot be cancelled: {len(running)} work order(s) are already in production.",
            {"work_orders": running},
        )
    plan.update(status="Cancelled", cancelled_by=user, cancelled_at=_now(), cancel_reason=reason)
    for ln in plan["lines"]:
        ln["status"] = "Cancelled"


# --- views ------------------------------------------------------------------------------------------------------------

_CAP_RANK = {s: i for i, s in enumerate(CAPACITY_STATUSES)}


def worst_capacity(lines: list[Doc]) -> str | None:
    return max((ln["capacity_status"] for ln in lines), key=_CAP_RANK.__getitem__, default=None)


def header(plan: Doc) -> dict[str, Any]:
    return {
        "plan_id": plan["plan_id"],
        "plan_number": plan["plan_number"],
        "baseline": bool(plan.get("baseline")),
        "period_start": plan["period_start"],
        "period_end": plan["period_end"],
        "status": display_status(plan) if not plan.get("baseline") else plan["status"],
        "capacity_status": worst_capacity(plan["lines"]),
        "line_count": len(plan["lines"]),
        "total_planned_qty": sum(int(ln["planned_qty"]) for ln in plan["lines"]),
        "created_by": plan.get("created_by"),
        "created_at": plan.get("created_at"),
    }


def summary(plan: Doc) -> dict[str, Any]:
    return {
        "line_count": len(plan["lines"]),
        "total_planned_qty": sum(int(ln["planned_qty"]) for ln in plan["lines"]),
        "total_demand_qty": sum(int(ln["demand_qty"]) + int(ln["forecast_qty"]) for ln in plan["lines"]),
        "work_order_count": len(plan["work_orders"]),
        "customer_orders": sorted({d["order_number"] for d in plan["demands"]}),
    }


def detail(plan: Doc) -> dict[str, Any]:
    lines = sorted(plan["lines"], key=lambda x: x["priority"])
    final = plan["status"] not in EDITABLE
    return {
        **{k: v for k, v in plan.items() if k not in ("session_id", "lines", "work_orders")},
        **header(plan),
        "stored_status": plan["status"],
        "editable": not final,
        "locked_reason": None if not final else f"This plan is {display_status(plan)}; a confirmed plan is a production commitment and does not change.",
        "lines": [
            {
                **ln,
                "product_name": orders.product_name(ln["sku"]),
                "sources": [d for d in plan["demands"] if d["line_id"] == ln["line_id"]],
            }
            for ln in lines
        ],
        "work_orders": [
            {**w, "status": wo_status(plan, w), "options": resource_options(w), "resourced": resourced(w), "product_name": orders.product_name(w["sku"])}
            for w in sorted(plan["work_orders"], key=lambda w: (w["priority"], w["sequence"]))
        ],
        "readiness": readiness(plan),
        "summary": summary(plan),
    }


def traceability(sid: str, order_ids: set[str]) -> list[dict[str, Any]]:
    """This session's plans serving the given order lines, in the order-detail traceability shape."""
    out = []
    for p in sorted(session_plans(sid), key=lambda p: str(p["created_at"])):
        rows = [d for d in p["demands"] if d["order_id"] in order_ids]
        if not rows:
            continue
        lines = {d["line_id"] for d in rows}
        out.append(
            {
                "plan_number": p["plan_number"],
                "plan_id": p["plan_id"],
                "month": p["period_start"][:7],
                "period": f"{p['period_start']} to {p['period_end']}",
                "status": display_status(p),
                "lines": [
                    {
                        "sku": ln["sku"],
                        "machine_id": next((w["machine_id"] for w in p["work_orders"] if w["line_id"] == ln["line_id"] and w["process"] == "MLD"), None),
                        "plan_qty": ln["planned_qty"],
                        "order_qty": sum(d["demand_qty"] for d in rows if d["line_id"] == ln["line_id"]),
                    }
                    for ln in p["lines"]
                    if ln["line_id"] in lines
                ],
                "work_orders": [
                    {
                        "wo_id": w["wo_id"],
                        "date": (w["planned_start"] or "")[:10] or None,
                        "machine_id": w["machine_id"],
                        "sku": w["sku"],
                        "qty": w["planned_qty"],
                        "status": wo_status(p, w),
                        "process": w["process_name"],
                    }
                    for w in p["work_orders"]
                    if w["line_id"] in lines
                ],
                "from_stock_qty": 0,
            }
        )
    return out

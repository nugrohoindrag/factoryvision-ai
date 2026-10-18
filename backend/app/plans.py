from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Any

from app import capacity, effects, forecasts, modules, plant
from app.errors import FVError
from app.store import store
from app.tools import planning
from app.tools.common import TODAY, to_date

PLAN_NOT_FOUND = "FV-PP-001"
INVALID_PERIOD = "FV-PP-002"
PLAN_LOCKED = "FV-PP-003"
ORDER_LINE_NOT_FOUND = "FV-PP-004"
ORDER_CANCELLED = "FV-PP-005"
DEMAND_QTY_INVALID = "FV-PP-006"
ALREADY_PLANNED = "FV-PP-007"
DEMAND_EXCEEDS = "FV-PP-008"
DEMAND_DUPLICATE = "FV-PP-009"
DEMAND_NOT_FOUND = "FV-PP-010"
LINE_NOT_FOUND = "FV-PP-011"
LINE_INVALID = "FV-PP-012"
STEP_LOCKED = "FV-PP-013"
STEP_INVALID = "FV-PP-014"
NOTHING_TO_GENERATE = "FV-PP-015"
ROUTING_INVALID = "FV-PP-016"
WO_NOT_FOUND = "FV-PP-017"
WO_INVALID = "FV-PP-018"
WO_TRANSITION = "FV-PP-019"
WO_NOT_READY = "FV-PP-020"
CONFIRM_NO_LINES = "FV-PP-021"
CONFIRM_CAPACITY = "FV-PP-022"
NO_WORK_ORDERS = "FV-PP-023"
CONFIRM_UNCONFIRMED = "FV-PP-024"
CONFIRM_STATE = "FV-PP-025"
CANCEL_REASON = "FV-PP-026"
CANCEL_BLOCKED = "FV-PP-027"
FORECAST_DEMAND_INVALID = "FV-PP-029"

PLANS = "production_plans"
WOS = "plan_work_orders"
EVENTS = "plan_events"

STATUSES = ("Draft", "Planning", "Ready", "Confirmed", "In Execution", "Completed", "Cancelled")
LOCKED = ("Confirmed", "In Execution", "Completed", "Cancelled")
COMMITTED = ("Confirmed", "In Execution", "Completed")
WO_STATUSES = ("Draft", "Confirmed", "Released", "In Production", "Completed")
WO_RUNNING = ("In Production", "Completed")
AGEING_DAYS = 7
MAX_PRIORITY = 5
STEP_NAMES = {1: "Demand", 2: "Production Plan", 3: "Work Order", 4: "Scheduling", 5: "Resource", 6: "Confirmation"}
PAINT_MACHINES = (planning.PAINT_LINE,)
ASSEMBLY_MACHINES = ("ASM-01", "ASM-02", "ASM-03")
EVENT_AUDIENCE = ("mes", "material", "delivery")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _wkey(sid: str, wo_id: str) -> str:
    return f"{sid}/{wo_id}"


def _fields(field: str, message: str) -> dict[str, list[dict[str, str]]]:
    return {"fields": [{"field": field, "message": message}]}


# --- reading --------------------------------------------------------------------------------------------------------


def _plans(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(PLANS, "session_id", sid), key=lambda p: (str(p["created_at"]), p["plan_number"]), reverse=True)


def _doc(sid: str, plan_id: str) -> dict[str, Any]:
    p = store().get(PLANS, plan_id)
    if p is None or p.get("session_id") != sid:
        raise FVError(PLAN_NOT_FOUND, f"Production plan {plan_id} does not exist in this session.")
    return p


def _wos(sid: str, plan_id: str) -> list[dict[str, Any]]:
    return [w for w in store().where(WOS, "session_id", sid) if w["plan_id"] == plan_id]


def _save(p: dict[str, Any]) -> None:
    p["updated_at"] = _now()
    store().set(PLANS, p["plan_id"], p)


def _save_wo(sid: str, w: dict[str, Any]) -> None:
    store().set(WOS, _wkey(sid, w["wo_id"]), w)


def planned_by_order_line(sid: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in store().where(PLANS, "session_id", sid):
        if p["status"] == "Cancelled":
            continue
        for d in p["demand"]:
            out[d["order_id"]] = out.get(d["order_id"], 0) + int(d["qty"])
    return out


def order_view(sid: str, o: effects.Overrides | None = None) -> dict[str, Any]:
    return modules.order_view(o or effects.overrides(sid), planned_by_order_line(sid))


def _order_lines(view: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {ln["order_id"]: (h, ln) for h in view["orders"] for ln in h["lines"]}


def plans_for_order(sid: str, order_number: str) -> list[dict[str, Any]]:
    return [
        {
            "plan_id": p["plan_id"],
            "plan_number": p["plan_number"],
            "status": p["status"],
            "qty": sum(d["qty"] for d in p["demand"] if d["order_number"] == order_number),
        }
        for p in _plans(sid)
        if any(d["order_number"] == order_number for d in p["demand"])
    ]


# --- rules ----------------------------------------------------------------------------------------------------------


def assert_editable(p: dict[str, Any]) -> None:
    if p["status"] in LOCKED:
        raise FVError(
            PLAN_LOCKED,
            f"Production plan {p['plan_number']} is {p['status']} and cannot be changed. A confirmed plan is a production commitment.",
            {"plan_id": p["plan_id"], "status": p["status"]},
        )


def next_number(existing: list[str], period_start: date) -> str:
    base = f"PP-{period_start:%Y-%m}"
    used = [1 if n == base else int(n.rsplit("-", 1)[1]) for n in existing if n == base or (n.startswith(base + "-") and n.rsplit("-", 1)[1].isdigit())]
    nxt = max(used, default=0) + 1
    return base if nxt == 1 else f"{base}-{nxt:03d}"


def requirement(line: dict[str, Any]) -> int:
    return int(line["demand_qty"]) + int(line["forecast_qty"])


def follow_planned(line: dict[str, Any], before: int) -> None:
    if line["planned_qty"] == before:
        line["planned_qty"] = requirement(line)


def resourced(w: dict[str, Any]) -> bool:
    if w["process"] == "Molding":
        return bool(w.get("machine_id") and w.get("mold_id"))
    return bool(w.get("machine_id"))


def scheduled(w: dict[str, Any]) -> bool:
    return bool(w.get("planned_start") and w.get("planned_end"))


def readiness(p: dict[str, Any], wos: list[dict[str, Any]]) -> dict[str, Any]:
    lines = p["lines"]
    demand_count = len(p["demand"]) + sum(1 for ln in lines if ln["forecast_qty"] > 0)
    planned_lines = sum(1 for ln in lines if ln["planned_qty"] > 0)
    n = len(wos)
    n_sched = sum(1 for w in wos if scheduled(w))
    n_res = sum(1 for w in wos if resourced(w))
    rules = [
        (1, True, None),
        (2, demand_count > 0, "Add at least one demand in Step 1 before setting planned quantities."),
        (3, planned_lines > 0, "Set a planned quantity on at least one plan line in Step 2 before generating work orders."),
        (4, n > 0, "Generate work orders in Step 3 before scheduling."),
        (
            5,
            n > 0 and n_sched > 0,
            "Generate work orders in Step 3 before assigning resources."
            if n == 0
            else "Schedule at least one work order in Step 4 before assigning resources.",
        ),
        (6, n > 0 and n_res >= n, "Generate work orders first." if n == 0 else f"{n - n_res} work order(s) have no machine and mold yet."),
    ]
    steps = [{"step": s, "name": STEP_NAMES[s], "ready": ok, "blocked_by": None if ok else why} for s, ok, why in rules]
    resume = 1
    for step_no, ok, _why in rules:
        if not ok:
            break
        resume = step_no
    return {
        "steps": steps,
        "resume_step": resume,
        "current_step": p["wizard_step"],
        "facts": {
            "demand_count": demand_count,
            "line_count": len(lines),
            "lines_with_planned_qty": planned_lines,
            "work_orders": n,
            "scheduled_work_orders": n_sched,
            "resourced_work_orders": n_res,
            "confirmed_work_orders": sum(1 for w in wos if w["confirmed"]),
            "capacity_up_required_lines": sum(1 for ln in lines if (ln.get("capacity") or {}).get("status") == capacity.UP),
        },
    }


def assert_step(step: int, r: dict[str, Any]) -> None:
    if not 1 <= step <= len(STEP_NAMES):
        raise FVError(STEP_INVALID, f"Wizard step {step} is not valid; use step 1 to 6.", _fields("step", "Use a step from 1 to 6."))
    if step <= r["current_step"]:
        return
    s = r["steps"][step - 1]
    if not s["ready"]:
        raise FVError(STEP_LOCKED, f"Step {step} ({s['name']}) cannot be opened yet. {s['blocked_by']}", {"step": step, "blocked_by": s["blocked_by"]})


def derived_status(p: dict[str, Any], wos: list[dict[str, Any]]) -> str:
    if p["status"] in LOCKED:
        if p["status"] in ("Confirmed", "In Execution") and wos:
            if all(w["status"] == "Completed" for w in wos):
                return "Completed"
            if any(w["status"] in WO_RUNNING for w in wos):
                return "In Execution"
        return str(p["status"])
    if not p["lines"]:
        return "Draft"
    ok = (
        wos
        and all(w["confirmed"] for w in wos)
        and not any((ln.get("capacity") or {}).get("status") == capacity.UP for ln in p["lines"])
        and any(ln["planned_qty"] > 0 for ln in p["lines"])
    )
    return "Ready" if ok else "Planning"


def routing(sku: str) -> list[dict[str, Any]]:
    return [
        {"sequence": 1, "process": "Molding", "resources": list(plant.MOLD_COMPAT.get(sku, []))},
        {"sequence": 2, "process": "Painting", "resources": list(PAINT_MACHINES)},
        {"sequence": 3, "process": "Assembly", "resources": list(ASSEMBLY_MACHINES)},
    ]


def routing_problems(sku: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not steps:
        return [{"sku": sku, "code": "NO_ROUTING", "message": f"{sku} has no process routing. Set one in the master data before generating work orders."}]
    out: list[dict[str, Any]] = []

    def problem(code: str, step: dict[str, Any], message: str) -> None:
        out.append({"sku": sku, "code": code, "process": step["process"], "sequence": step["sequence"], "message": message})

    for st in steps:
        if not st.get("active", True):
            problem("ROUTING_STEP_INACTIVE", st, f"{sku}: routing step {st['sequence']} ({st['process']}) is inactive, so the process chain is broken.")
    for st in steps:
        if not st.get("process_active", True):
            problem("PROCESS_INACTIVE", st, f"{sku}: process {st['process']} is inactive. Reactivate it or change the product routing.")
    active = sorted((st for st in steps if st.get("active", True)), key=lambda st: st["sequence"])
    seen: set[int] = set()
    for st in active:
        if st["sequence"] in seen:
            problem(
                "SEQUENCE_DUPLICATE", st, f"{sku}: sequence {st['sequence']} is used by more than one process ({st['process']}), so the order is ambiguous."
            )
        seen.add(st["sequence"])
    if active and active[0]["sequence"] != 1:
        problem(
            "SEQUENCE_NOT_CONTINUOUS", active[0], f"{sku}: the routing starts at sequence {active[0]['sequence']}, not 1. The first process must be sequence 1."
        )
    for prev, cur in pairwise(active):
        if cur["sequence"] != prev["sequence"] + 1:
            problem(
                "SEQUENCE_NOT_CONTINUOUS",
                cur,
                f"{sku}: the sequence jumps from {prev['sequence']} ({prev['process']}) to {cur['sequence']} ({cur['process']}); no process sits between them.",
            )
    for st in active:
        if not st["resources"]:
            problem("NO_MAPPABLE_RESOURCE", st, f"{sku}: process {st['process']} has no machine or work center that can run it. Map one in the master data.")
    return out


def wo_number(period_start: date, machine: str, sku: str, taken: set[str]) -> str:
    base = f"WO-{period_start:%y%m%d}-{machine}-{sku}"
    k, wo_id = 1, base
    while wo_id in taken:
        k += 1
        wo_id = f"{base}-{k}"
    return wo_id


def wo_checklist(w: dict[str, Any], others: list[dict[str, Any]]) -> list[str]:
    reasons = []
    if not w.get("qty") or w["qty"] <= 0:
        reasons.append("Planned quantity is not set.")
    if not scheduled(w):
        reasons.append("The schedule (planned start and end) is incomplete.")
    if not w.get("machine_id"):
        reasons.append("No machine assigned.")
    if w["process"] == "Molding" and not w.get("mold_id"):
        reasons.append("No mold assigned.")
    if not w.get("shift"):
        reasons.append("No shift assigned.")
    if scheduled(w) and w.get("machine_id"):
        reasons.extend(
            f"Open conflict must be resolved: {w['machine_id']} is already booked by {o['wo_id']} from {o['planned_start']} to {o['planned_end']}."
            for o in others
            if o["wo_id"] != w["wo_id"]
            and o.get("machine_id") == w["machine_id"]
            and scheduled(o)
            and o["planned_start"] < w["planned_end"]
            and w["planned_start"] < o["planned_end"]
        )
    return reasons


def check_resource(w: dict[str, Any], machine_id: str | None, mold_id: str | None) -> list[dict[str, str]]:
    errs = []
    sku = w["sku"]
    if w["process"] == "Molding":
        compat = plant.MOLD_COMPAT[sku]
        if machine_id is not None and machine_id not in compat:
            errs.append({"field": "machine_id", "message": f"{machine_id} cannot run {sku}: its mold fits only {', '.join(compat)}."})
        if mold_id is not None and mold_id != plant.MOLD_ID[sku]:
            errs.append({"field": "mold_id", "message": f"Mold {mold_id} is not a mold for {sku}; use {plant.MOLD_ID[sku]}."})
    else:
        allowed = PAINT_MACHINES if w["process"] == "Painting" else ASSEMBLY_MACHINES
        if machine_id is not None and machine_id not in allowed:
            errs.append({"field": "machine_id", "message": f"{w['process']} runs on {', '.join(allowed)}, not {machine_id}."})
        if mold_id:
            errs.append({"field": "mold_id", "message": f"{w['process']} uses no mold."})
    return errs


def _dt(v: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=plant.WIB)


# --- capacity -------------------------------------------------------------------------------------------------------


def _assess(p: dict[str, Any], o: effects.Overrides) -> None:
    if not p["lines"]:
        return
    demand = {ln["sku"]: int(ln["planned_qty"]) for ln in p["lines"]}
    caps = capacity.sku_capacity(
        demand, to_date(p["period_start"]), to_date(p["period_end"]), float(p["utilization_pct"]), maintenance=o.maintenance, mold_moves=o.mold_moves
    )
    for ln in p["lines"]:
        ln["capacity"] = caps[ln["sku"]]


def _refresh(sid: str, p: dict[str, Any], o: effects.Overrides | None = None) -> dict[str, Any]:
    if p["status"] not in LOCKED:
        _assess(p, o or effects.overrides(sid))
    p["status"] = derived_status(p, _wos(sid, p["plan_id"]))
    _save(p)
    return p


def _gap_pct(line: dict[str, Any] | None) -> float | None:
    if not line or not line.get("capacity") or not line["capacity"]["total"]:
        return None
    return round(float(line["capacity"]["utilization_pct"]) - 100, 1)


# --- detail ---------------------------------------------------------------------------------------------------------


def _age_days(p: dict[str, Any]) -> int:
    return max(0, (datetime.now(UTC) - datetime.fromisoformat(p["created_at"])).days)


def header(p: dict[str, Any]) -> dict[str, Any]:
    age = _age_days(p)
    return {
        **{k: v for k, v in p.items() if k not in ("lines", "demand")},
        "capacity_status": capacity.worst((ln.get("capacity") or {}).get("status") for ln in p["lines"]),
        "line_count": len(p["lines"]),
        "total_planned": sum(int(ln["planned_qty"]) for ln in p["lines"]),
        "age_days": age,
        "ageing": p["status"] == "Draft" and age >= AGEING_DAYS,
        "ageing_note": "This draft is not a commitment yet: its demand is not planned for production."
        if p["status"] == "Draft" and age >= AGEING_DAYS
        else None,
    }


def _sorted_wos(p: dict[str, Any], wos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prio = {ln["line_id"]: (ln["priority"], ln["sku"]) for ln in p["lines"]}
    return sorted(wos, key=lambda w: (*prio.get(w["line_id"], (9, w["sku"])), w["routing_step"]))


def detail(sid: str, p: dict[str, Any]) -> dict[str, Any]:
    wos = _sorted_wos(p, _wos(sid, p["plan_id"]))
    orders_ = sorted({d["order_number"] for d in p["demand"]})
    return {
        **p,
        "capacity_status": capacity.worst((ln.get("capacity") or {}).get("status") for ln in p["lines"]),
        "locked": p["status"] in LOCKED,
        "readiness": readiness(p, wos),
        "work_orders": wos,
        "summary": {
            "lines": len(p["lines"]),
            "total_planned": sum(int(ln["planned_qty"]) for ln in p["lines"]),
            "total_demand": sum(int(ln["demand_qty"]) for ln in p["lines"]),
            "total_forecast": sum(int(ln["forecast_qty"]) for ln in p["lines"]),
            "work_orders": len(wos),
            "customer_orders": len(orders_),
            "customer_order_numbers": orders_,
        },
    }


def list_plans(sid: str, status: str | None = None, period_from: str | None = None, period_to: str | None = None) -> list[dict[str, Any]]:
    out = []
    for p in _plans(sid):
        if status and p["status"] not in status.split(","):
            continue
        if period_from and p["period_end"] < period_from:
            continue
        if period_to and p["period_start"] > period_to:
            continue
        out.append(header(p))
    return out


def get_plan(sid: str, plan_id: str) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    if p["status"] in LOCKED:
        new = derived_status(p, _wos(sid, plan_id))
        if new != p["status"]:
            p["status"] = new
            _save(p)
    return detail(sid, p)


# --- create ---------------------------------------------------------------------------------------------------------


def create_plan(sid: str, period_start: str | None, period_end: str | None, *, user: str = "You", origin: str = "user") -> dict[str, Any]:
    start, end = capacity.parse_period(period_start, period_end, INVALID_PERIOD)
    try:
        forecast_id: str | None = str(forecasts.latest(sid)["forecast_id"])
    except (FVError, IndexError, KeyError):
        forecast_id = None
    now = _now()
    p: dict[str, Any] = {
        "plan_id": uuid.uuid4().hex,
        "plan_number": next_number([x["plan_number"] for x in store().where(PLANS, "session_id", sid)], start),
        "session_id": sid,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "status": "Draft",
        "utilization_pct": capacity.utilization_pct(sid),
        "forecast_id": forecast_id,
        "created_at": now,
        "created_by": user,
        "updated_at": now,
        "confirmed_at": None,
        "confirmed_by": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "cancel_reason": None,
        "wizard_step": 1,
        "origin": origin,
        "lines": [],
        "demand": [],
    }
    _save(p)
    return detail(sid, p)


# --- step 1: demand -------------------------------------------------------------------------------------------------


def demand_candidates(sid: str, plan_id: str) -> list[dict[str, Any]]:
    p = _doc(sid, plan_id)
    out = []
    for h in order_view(sid)["orders"]:
        if h["status"] == "Cancelled":
            continue
        for ln in h["lines"]:
            remaining = int(ln["ordered_qty"]) - int(ln["planned_qty"])
            if ln["status"] == "Cancelled" or remaining <= 0:
                continue
            out.append(
                {
                    "order_id": ln["order_id"],
                    "order_number": h["order_number"],
                    "line_no": ln["line_no"],
                    "customer_id": h["customer_id"],
                    "customer_name": h["customer_name"],
                    "sku": ln["sku"],
                    "product_name": ln["product_name"],
                    "ordered_qty": ln["ordered_qty"],
                    "planned_qty": ln["planned_qty"],
                    "remaining_qty": remaining,
                    "requested_delivery_date": ln["requested_delivery_date"],
                    "order_status": h["status"],
                    "in_period": p["period_start"] <= ln["requested_delivery_date"] <= p["period_end"],
                }
            )
    return sorted(out, key=lambda c: (c["requested_delivery_date"], c["order_number"], c["line_no"]))


def _new_line(p: dict[str, Any], sku: str, required: str | None) -> dict[str, Any]:
    line = {
        "line_id": f"L-{uuid.uuid4().hex[:10]}",
        "sku": sku,
        "product_name": capacity.product_name(sku),
        "demand_qty": 0,
        "forecast_qty": 0,
        "forecast_line_ids": [],
        "planned_qty": 0,
        "priority": min(len(p["lines"]) + 1, MAX_PRIORITY),
        "required_date": required,
        "status": "Open",
        "capacity": None,
    }
    p["lines"].append(line)
    return line


def _line_of(p: dict[str, Any], sku: str) -> dict[str, Any] | None:
    return next((ln for ln in p["lines"] if ln["sku"] == sku), None)


def add_demand(sid: str, plan_id: str, order_id: str, qty: int | None = None) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    o = effects.overrides(sid)
    found = _order_lines(order_view(sid, o)).get(order_id)
    if found is None:
        raise FVError(ORDER_LINE_NOT_FOUND, f"Customer order line {order_id} does not exist.")
    h, ol = found
    if h["status"] == "Cancelled" or ol["status"] == "Cancelled":
        raise FVError(ORDER_CANCELLED, f"Customer order {h['order_number']} is cancelled and cannot enter a production plan.")
    ordered, planned = int(ol["ordered_qty"]), int(ol["planned_qty"])
    outstanding = ordered - planned
    q = outstanding if qty is None else int(qty)
    if q <= 0:
        if outstanding <= 0:
            raise FVError(
                ALREADY_PLANNED,
                f"Order line {order_id} is already fully planned ({planned} of {ordered}).",
                _fields("qty", f"The remaining quantity of order line {order_id} is {outstanding}; nothing is left to plan."),
            )
        raise FVError(DEMAND_QTY_INVALID, "Demand quantity must be more than zero.", _fields("qty", f"Demand quantity {q} is not valid."))
    if q > outstanding:
        raise FVError(
            DEMAND_EXCEEDS,
            "Demand exceeds the remaining quantity of the order line.",
            _fields("qty", f"Demand {q} is more than the remaining {outstanding} on the order line (ordered {ordered}, already planned {planned})."),
        )
    dup = next((d for d in p["demand"] if d["order_id"] == order_id), None)
    if dup:
        raise FVError(DEMAND_DUPLICATE, f"Order line {order_id} is already in this plan on the {dup['sku']} plan line.", {"line_id": dup["line_id"]})
    rd = ol["requested_delivery_date"]
    line = _line_of(p, ol["sku"]) or _new_line(p, ol["sku"], rd)
    before = requirement(line)
    line["demand_qty"] = int(line["demand_qty"]) + q
    if line["required_date"] is None or rd < line["required_date"]:
        line["required_date"] = rd
    follow_planned(line, before)
    p["demand"].append(
        {
            "order_id": order_id,
            "order_number": h["order_number"],
            "line_no": ol["line_no"],
            "customer_id": h["customer_id"],
            "customer_name": h["customer_name"],
            "sku": ol["sku"],
            "qty": q,
            "requested_delivery_date": rd,
            "line_id": line["line_id"],
        }
    )
    _sync_line_wos(sid, line)
    return detail(sid, _refresh(sid, p, o))


def _drop_line(sid: str, p: dict[str, Any], line: dict[str, Any]) -> None:
    p["lines"] = [ln for ln in p["lines"] if ln["line_id"] != line["line_id"]]
    for w in _wos(sid, p["plan_id"]):
        if w["line_id"] == line["line_id"]:
            store().delete(WOS, _wkey(sid, w["wo_id"]))


def _sync_line_wos(sid: str, line: dict[str, Any]) -> None:
    for w in store().where(WOS, "session_id", sid):
        if w["line_id"] == line["line_id"] and w["qty"] != line["planned_qty"]:
            w.update(qty=int(line["planned_qty"]), confirmed=False, status="Draft", planned_start=None, planned_end=None, shift=None, sequence=None)
            _save_wo(sid, w)


def _after_demand_change(sid: str, p: dict[str, Any], line: dict[str, Any]) -> None:
    rows = [d for d in p["demand"] if d["line_id"] == line["line_id"]]
    if not rows and line["forecast_qty"] <= 0:
        _drop_line(sid, p, line)
        return
    line["demand_qty"] = sum(int(d["qty"]) for d in rows)
    line["planned_qty"] = min(int(line["planned_qty"]), requirement(line))
    if rows:
        line["required_date"] = min(d["requested_delivery_date"] for d in rows)
    _sync_line_wos(sid, line)


def remove_demand(sid: str, plan_id: str, order_id: str) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    row = next((d for d in p["demand"] if d["order_id"] == order_id), None)
    if row is None:
        raise FVError(DEMAND_NOT_FOUND, f"Order line {order_id} is not demand of production plan {p['plan_number']}.")
    p["demand"] = [d for d in p["demand"] if d["order_id"] != order_id]
    line = next((ln for ln in p["lines"] if ln["line_id"] == row["line_id"]), None)
    if line:
        _after_demand_change(sid, p, line)
    return detail(sid, _refresh(sid, p))


def demand_breakdown(sid: str, plan_id: str) -> list[dict[str, Any]]:
    p = _doc(sid, plan_id)
    return [
        {
            "line_id": ln["line_id"],
            "sku": ln["sku"],
            "product_name": ln["product_name"],
            "demand_qty": ln["demand_qty"],
            "forecast_qty": ln["forecast_qty"],
            "planned_qty": ln["planned_qty"],
            "required_date": ln["required_date"],
            "sources": sorted(
                (
                    {k: d[k] for k in ("order_id", "order_number", "line_no", "customer_id", "customer_name", "qty", "requested_delivery_date")}
                    for d in p["demand"]
                    if d["line_id"] == ln["line_id"]
                ),
                key=lambda s: (s["order_number"], s["line_no"]),
            ),
        }
        for ln in p["lines"]
    ]


def unordered_forecast(sid: str, p: dict[str, Any], sku: str, view: dict[str, Any] | None = None) -> tuple[int, int, list[str], dict[str, Any]]:
    try:
        snap = forecasts.get_snapshot(sid, p["forecast_id"]) if p.get("forecast_id") else forecasts.latest(sid)
    except FVError as e:
        raise FVError(FORECAST_DEMAND_INVALID, "No forecast snapshot is available for this plan.", {"forecast_id": p.get("forecast_id")}) from e
    fq, ids = forecasts.qty_for(snap, sku, p["period_start"], p["period_end"])
    view = view or order_view(sid)
    ordered = sum(
        int(ln["ordered_qty"])
        for h in view["orders"]
        if h["status"] != "Cancelled"
        for ln in h["lines"]
        if ln["sku"] == sku and ln["status"] != "Cancelled" and p["period_start"] <= ln["requested_delivery_date"] <= p["period_end"]
    )
    return max(fq - ordered, 0), fq, ids, snap


def set_forecast_demand(sid: str, plan_id: str, sku: str, qty: int | None = None) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    if sku not in plant.PRODUCT_BY_SKU:
        raise FVError(FORECAST_DEMAND_INVALID, f"Unknown SKU {sku}.", _fields("sku", f"Use one of {', '.join(plant.SKUS)}."))
    available, fq, ids, snap = unordered_forecast(sid, p, sku)
    q = available if qty is None else int(qty)
    if q < 0:
        raise FVError(FORECAST_DEMAND_INVALID, "Forecast quantity cannot be negative.", _fields("qty", "Use 0 or more."))
    if q > available:
        raise FVError(
            FORECAST_DEMAND_INVALID,
            f"Forecast demand {q} is more than the unordered forecast {available} for {sku} in the plan period.",
            _fields("qty", f"Forecast {fq} in snapshot {snap['forecast_id']} minus orders already received leaves {available}."),
        )
    p["forecast_id"] = snap["forecast_id"]
    line = _line_of(p, sku)
    if line is None:
        if q == 0:
            return detail(sid, _refresh(sid, p))
        line = _new_line(p, sku, p["period_end"])
    before = requirement(line)
    line["forecast_qty"] = q
    line["forecast_line_ids"] = ids if q else []
    follow_planned(line, before)
    _after_demand_change(sid, p, line)
    return detail(sid, _refresh(sid, p))


# --- step 2: decision -----------------------------------------------------------------------------------------------


def update_line(sid: str, plan_id: str, line_id: str, planned_qty: int | None = None, priority: int | None = None) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    line = next((ln for ln in p["lines"] if ln["line_id"] == line_id), None)
    if line is None:
        raise FVError(LINE_NOT_FOUND, f"Plan line {line_id} does not exist on production plan {p['plan_number']}.")
    errs = []
    if planned_qty is not None and planned_qty < 0:
        errs.append({"field": "planned_qty", "message": "Planned quantity must be 0 or more."})
    if priority is not None and not 1 <= priority <= MAX_PRIORITY:
        errs.append({"field": "priority", "message": f"Priority must be between 1 and {MAX_PRIORITY}."})
    if errs:
        msg = "Planned quantity cannot be negative." if errs[0]["field"] == "planned_qty" else errs[0]["message"]
        raise FVError(LINE_INVALID, msg, {"fields": errs})
    if planned_qty is not None:
        line["planned_qty"] = int(planned_qty)
        _sync_line_wos(sid, line)
    if priority is not None:
        line["priority"] = int(priority)
    return detail(sid, _refresh(sid, p))


def set_step(sid: str, plan_id: str, step: int) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    if not 1 <= step <= len(STEP_NAMES):
        assert_step(step, readiness(p, []))
    if p["status"] in LOCKED:
        return detail(sid, p)
    assert_step(step, readiness(p, _wos(sid, plan_id)))
    p["wizard_step"] = step
    _save(p)
    return detail(sid, p)


# --- step 3-5: work orders ------------------------------------------------------------------------------------------


def generate_work_orders(sid: str, plan_id: str, routes: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    if p["status"] in LOCKED:
        raise FVError(PLAN_LOCKED, f"Production plan {p['plan_number']} is {p['status']}; work orders cannot be generated again.", {"status": p["status"]})
    if not p["lines"]:
        raise FVError(NOTHING_TO_GENERATE, "The production plan has no plan lines yet.")
    lines = sorted(p["lines"], key=lambda ln: (ln["priority"], ln["sku"]))
    todo = [ln for ln in lines if ln["planned_qty"] > 0]
    routes = routes or {ln["sku"]: routing(ln["sku"]) for ln in todo}
    problems = [x for ln in todo for x in routing_problems(ln["sku"], routes.get(ln["sku"], []))]
    if problems:
        raise FVError(ROUTING_INVALID, "Work order generation was cancelled because a routing is invalid; no work orders were created.", {"problems": problems})
    o = effects.overrides(sid)
    home = capacity.homes(o.mold_moves)
    existing = _wos(sid, plan_id)
    taken = {w["wo_id"] for w in store().where(WOS, "session_id", sid)}
    start = to_date(p["period_start"])
    created: list[dict[str, Any]] = []
    existed: list[dict[str, Any]] = []
    for ln in todo:
        pred = None
        for step in sorted((st for st in routes[ln["sku"]] if st.get("active", True)), key=lambda st: st["sequence"]):
            old = next((w for w in existing if w["line_id"] == ln["line_id"] and w["process"] == step["process"]), None)
            if old:
                existed.append(old)
                pred = old["wo_id"]
                continue
            mounted = home.get(ln["sku"]) if step["process"] == "Molding" else None
            default = str(mounted if mounted in step["resources"] else step["resources"][0])
            wo_id = wo_number(start, default, ln["sku"], taken)
            taken.add(wo_id)
            w = {
                "wo_id": wo_id,
                "session_id": sid,
                "plan_id": plan_id,
                "plan_number": p["plan_number"],
                "line_id": ln["line_id"],
                "sku": ln["sku"],
                "process": step["process"],
                "routing_step": step["sequence"],
                "sequence": None,
                "predecessor_wo_id": pred,
                "qty": int(ln["planned_qty"]),
                "priority": ln["priority"],
                "required_date": ln["required_date"],
                "compatible_machines": list(step["resources"]),
                "compatible_molds": [plant.MOLD_ID[ln["sku"]]] if step["process"] == "Molding" else [],
                "machine_id": None,
                "mold_id": None,
                "planned_start": None,
                "planned_end": None,
                "shift": None,
                "status": "Draft",
                "confirmed": False,
                "created_at": _now(),
            }
            _save_wo(sid, w)
            created.append(w)
            pred = wo_id
    if created:
        _event(sid, "WorkOrdersGenerated", p, {"created": [w["wo_id"] for w in created], "existed": [w["wo_id"] for w in existed]})
    p = _refresh(sid, p, o)
    skipped = [ln["line_id"] for ln in lines if ln["planned_qty"] <= 0]
    if created:
        msg = f"{len(created)} work order(s) created."
    elif existed:
        msg = "Every work order already exists; generating again created no duplicates."
    else:
        msg = "No plan line has a planned quantity above zero, so no work order was created."
    return {
        "created": created,
        "existed": existed,
        "skipped_line_ids": skipped,
        "message": msg,
        "work_orders": _sorted_wos(p, _wos(sid, plan_id)),
        "plan": detail(sid, p),
    }


def schedule(sid: str, plan_id: str, time_limit_s: float = 10.0) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    wos = _wos(sid, plan_id)
    if not wos:
        raise FVError(NO_WORK_ORDERS, "No work orders have been generated for this plan yet. Generate work orders in Step 3 first.")
    o = effects.overrides(sid)
    start, end = to_date(p["period_start"]), to_date(p["period_end"])
    home = capacity.homes(o.mold_moves)
    cycles = capacity.cycle_seconds(planning.machine_rates(), o.maintenance)
    blocked = {mid: int(m) for mid, m in capacity.planned_downtime(start, end, o.maintenance).items()} if start <= TODAY else {}
    jobs = [
        {
            "wo_id": w["wo_id"],
            "sku": w["sku"],
            "process": w["process"],
            "qty": int(w["qty"]),
            "due": to_date(w.get("required_date") or p["period_end"]),
            "priority": w.get("priority", 3),
            "predecessor_wo_id": w["predecessor_wo_id"],
        }
        for w in wos
    ]
    r = planning.schedule_work_orders(jobs, max(start, TODAY) if start <= TODAY <= end else start, cycles, home, blocked=blocked, time_limit_s=time_limit_s)
    if "error" in r:
        err = r["error"]
        raise FVError(str(err["code"]), str(err["message"]), err["detail"])
    by_id = {a["wo_id"]: a for a in r["assignments"]}
    reset = 0
    for w in wos:
        a = by_id[w["wo_id"]]
        reset += 1 if w["confirmed"] else 0
        w.update(
            machine_id=a["machine_id"],
            mold_id=a["mold_id"],
            planned_start=a["planned_start"],
            planned_end=a["planned_end"],
            shift=a["shift"],
            sequence=a["sequence"],
            late_minutes=a["late_minutes"],
            mold_moved=a["mold_moved"],
            confirmed=False,
            status="Draft",
        )
        _save_wo(sid, w)
    if p["wizard_step"] < 4:
        p["wizard_step"] = 4
    p = _refresh(sid, p, o)
    return {
        "status": r["status"],
        "solve_seconds": r["solve_seconds"],
        "engine": r["engine"],
        "late_work_orders": r["late_work_orders"],
        "unconfirmed": reset,
        "assumptions": r["assumptions"],
        "work_orders": _sorted_wos(p, _wos(sid, plan_id)),
    }


def _wo(sid: str, plan_id: str, wo_id: str) -> dict[str, Any]:
    w = store().get(WOS, _wkey(sid, wo_id))
    if w is None or w["plan_id"] != plan_id:
        raise FVError(WO_NOT_FOUND, f"Work order {wo_id} does not belong to this plan.")
    return w


def update_work_order(sid: str, plan_id: str, wo_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    w = _wo(sid, plan_id, wo_id)
    errs = check_resource(w, changes.get("machine_id"), changes.get("mold_id"))
    start = changes.get("planned_start", w.get("planned_start"))
    end = changes.get("planned_end", w.get("planned_end"))
    for f in ("planned_start", "planned_end"):
        if changes.get(f) is not None and _dt(str(changes[f])) is None:
            errs.append({"field": f, "message": "Not a date-time."})
    if start and end and _dt(str(start)) and _dt(str(end)) and _dt(str(end)) <= _dt(str(start)):  # type: ignore[operator]
        errs.append({"field": "planned_end", "message": "Planned end must be after planned start."})
    if changes.get("shift") is not None and changes["shift"] not in {s["shift_id"] for s in plant.SHIFTS}:
        errs.append({"field": "shift", "message": "Shift must be A, B or C."})
    if errs:
        raise FVError(WO_INVALID, errs[0]["message"], {"fields": errs})
    for f in ("machine_id", "mold_id", "shift"):
        if f in changes and changes[f] is not None:
            w[f] = changes[f]
    for f in ("planned_start", "planned_end"):
        if changes.get(f) is not None:
            w[f] = _dt(str(changes[f])).isoformat()  # type: ignore[union-attr]
    w.update(confirmed=False, status="Draft")
    _save_wo(sid, w)
    _refresh(sid, p)
    return w


def confirm_work_orders(sid: str, plan_id: str) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    assert_editable(p)
    wos = _wos(sid, plan_id)
    if not wos:
        raise FVError(NO_WORK_ORDERS, "No work orders have been generated for this plan yet. Generate work orders in Step 3 first.")
    problems = [{"wo_id": w["wo_id"], "reasons": r} for w in wos if not w["confirmed"] and (r := wo_checklist(w, wos))]
    if problems:
        raise FVError(WO_NOT_READY, f"{len(problems)} work order(s) are not ready to confirm; none was confirmed.", {"work_orders": problems})
    for w in wos:
        if not w["confirmed"]:
            w.update(confirmed=True, status="Confirmed")
            _save_wo(sid, w)
    return detail(sid, _refresh(sid, p))


def advance_work_order(sid: str, plan_id: str, wo_id: str, status: str) -> dict[str, Any]:
    p = _doc(sid, plan_id)
    w = _wo(sid, plan_id, wo_id)
    if p["status"] not in ("Confirmed", "In Execution"):
        raise FVError(WO_TRANSITION, "Work orders move to production only after the plan is confirmed.", {"plan_status": p["status"]})
    if status not in WO_STATUSES or WO_STATUSES.index(status) <= WO_STATUSES.index(w["status"]):
        raise FVError(WO_TRANSITION, f"Work order {wo_id} cannot move from {w['status']} to {status}.", {"valid": list(WO_STATUSES)})
    w["status"] = status
    _save_wo(sid, w)
    p["status"] = derived_status(p, _wos(sid, plan_id))
    _save(p)
    return detail(sid, p)


# --- step 6: confirm, cancel ----------------------------------------------------------------------------------------


def confirm(sid: str, plan_id: str, user: str = "You") -> dict[str, Any]:
    p = _doc(sid, plan_id)
    if p["status"] in ("Confirmed", "In Execution"):
        raise FVError(CONFIRM_STATE, "The production plan is already confirmed.")
    if p["status"] in ("Cancelled", "Completed"):
        raise FVError(CONFIRM_STATE, f"A {p['status']} production plan cannot be confirmed.")
    if not p["lines"]:
        raise FVError(CONFIRM_NO_LINES, "A production plan without plan lines cannot be confirmed.")
    _assess(p, effects.overrides(sid))
    blocked = sorted(ln["sku"] for ln in p["lines"] if ln["capacity"]["status"] == capacity.UP)
    if blocked:
        _save(p)
        raise FVError(
            CONFIRM_CAPACITY,
            f"{len(blocked)} plan line(s) are Capacity Up Required: {', '.join(blocked)}. Request more capacity or lower the planned quantity before confirming.",
            {"skus": blocked},
        )
    wos = _wos(sid, plan_id)
    if not wos:
        raise FVError(NO_WORK_ORDERS, "No work orders have been generated for this plan yet. Generate work orders first.")
    pending = sum(1 for w in wos if not w["confirmed"])
    if pending:
        raise FVError(
            CONFIRM_UNCONFIRMED, f"{pending} work order(s) are not confirmed yet. Confirm every work order before confirming the plan.", {"count": pending}
        )
    p.update(status="Confirmed", confirmed_at=_now(), confirmed_by=user, wizard_step=6)
    for ln in p["lines"]:
        ln["status"] = "Released"
    _save(p)
    orders_ = sorted({d["order_number"] for d in p["demand"]})
    _event(
        sid,
        "ProductionPlanConfirmed",
        p,
        {
            "period_start": p["period_start"],
            "period_end": p["period_end"],
            "confirmed_by": user,
            "customer_orders": orders_,
            "planned_total": sum(int(ln["planned_qty"]) for ln in p["lines"]),
            "lines": [{"sku": ln["sku"], "planned_qty": ln["planned_qty"], "required_date": ln["required_date"]} for ln in p["lines"]],
        },
    )
    return detail(sid, p)


def cancel(sid: str, plan_id: str, reason: str, user: str = "You") -> dict[str, Any]:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise FVError(CANCEL_REASON, "A cancellation reason is required.", _fields("reason", "Give a reason of at least 3 characters."))
    p = _doc(sid, plan_id)
    if p["status"] == "Cancelled":
        raise FVError(CANCEL_BLOCKED, "The production plan is already cancelled.")
    running = sum(1 for w in _wos(sid, plan_id) if w["status"] in WO_RUNNING)
    if running:
        raise FVError(
            CANCEL_BLOCKED, f"The production plan cannot be cancelled: {running} work order(s) are already in production or completed.", {"count": running}
        )
    p.update(status="Cancelled", cancelled_at=_now(), cancelled_by=user, cancel_reason=reason)
    _save(p)
    _event(sid, "ProductionPlanCancelled", p, {"reason": reason, "released_demand": [{"order_id": d["order_id"], "qty": d["qty"]} for d in p["demand"]]})
    return detail(sid, p)


# --- re-plan (agentic) ----------------------------------------------------------------------------------------------


def replan(sid: str, *, reason: str, forecast_id: str | None = None, demand_change_pct: dict[str, float] | None = None, user: str = "agent") -> dict[str, Any]:
    start, end = capacity.month_bounds(TODAY)
    prev = next((x for x in _plans(sid) if x["status"] != "Cancelled"), None)
    created = create_plan(sid, start.isoformat(), end.isoformat(), user=user, origin="agent")
    p = _doc(sid, created["plan_id"])
    if forecast_id:
        p["forecast_id"] = forecasts.get_snapshot(sid, forecast_id)["forecast_id"]
    p["replan_reason"] = reason
    p["demand_change_pct"] = dict(demand_change_pct or {})
    _save(p)
    for c in demand_candidates(sid, p["plan_id"]):
        if c["in_period"]:
            add_demand(sid, p["plan_id"], c["order_id"])
    p = _doc(sid, p["plan_id"])
    view = order_view(sid)
    for sku in plant.SKUS:
        try:
            available, *_ = unordered_forecast(sid, p, sku, view)
        except FVError:
            break
        if available:
            set_forecast_demand(sid, p["plan_id"], sku, available)
    p = _doc(sid, p["plan_id"])
    o = effects.overrides(sid)
    _assess(p, o)
    baseline = {ln["sku"]: _gap_pct(ln) for ln in p["lines"]}
    for ln in p["lines"]:
        chg = float((demand_change_pct or {}).get(ln["sku"], 0.0))
        if chg:
            ln["planned_qty"] = round(requirement(ln) * (1 + chg / 100))
    p = _refresh(sid, p, o)
    before = {ln["sku"]: _gap_pct(ln) for ln in prev["lines"]} if prev else baseline
    delta = {ln["sku"]: {"before_gap_pct": before.get(ln["sku"]), "after_gap_pct": _gap_pct(ln)} for ln in p["lines"]}
    return {**detail(sid, p), "capacity_delta": delta, "compared_with": prev["plan_number"] if prev else "the same demand without the change"}


# --- events and downstream ------------------------------------------------------------------------------------------


def _event(sid: str, event_type: str, p: dict[str, Any], payload: dict[str, Any]) -> None:
    eid = uuid.uuid4().hex
    store().set(
        EVENTS,
        f"{sid}/{eid}",
        {
            "event_id": eid,
            "session_id": sid,
            "event_type": event_type,
            "plan_id": p["plan_id"],
            "plan_number": p["plan_number"],
            "at": _now(),
            "audience": list(EVENT_AUDIENCE),
            "payload": payload,
        },
    )


def events(sid: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = sorted(store().where(EVENTS, "session_id", sid), key=lambda e: str(e["at"]), reverse=True)
    return rows[:limit]


def downstream(sid: str, module: str) -> dict[str, Any]:
    committed = [p for p in _plans(sid) if p["status"] in COMMITTED]
    out: dict[str, Any] = {
        "events": events(sid),
        "committed_plans": [{k: p[k] for k in ("plan_id", "plan_number", "status", "period_start", "period_end", "confirmed_at")} for p in committed],
    }
    if module == "material":
        need: dict[str, float] = {}
        for p in committed:
            for ln in p["lines"]:
                for b in plant.bom_lines():
                    if b["sku"] == ln["sku"]:
                        need[str(b["material_id"])] = need.get(str(b["material_id"]), 0.0) + float(b["qty_per_unit"]) * int(ln["planned_qty"])  # type: ignore[arg-type]
        out["requirements"] = [
            {"material_id": m, "material_name": plant.MATERIAL_BY_ID[m].material_name, "unit": plant.MATERIAL_BY_ID[m].unit, "qty": round(q, 1)}
            for m, q in sorted(need.items())
        ]
        out["note"] = "Bill-of-materials explosion of the planned quantity of confirmed production plans."
    elif module == "delivery":
        out["supply"] = [
            {"plan_number": p["plan_number"], "sku": ln["sku"], "planned_qty": ln["planned_qty"], "required_date": ln["required_date"]}
            for p in committed
            for ln in p["lines"]
        ]
        out["note"] = "Planned output of confirmed production plans, by the date the earliest order on each line needs it."
    elif module == "mes":
        out["work_orders"] = [w for p in committed for w in _sorted_wos(p, _wos(sid, p["plan_id"]))]
        out["note"] = "Work orders of confirmed production plans, in plan priority and routing order."
    return out


def clear(sid: str) -> None:
    for p in store().where(PLANS, "session_id", sid):
        store().delete(PLANS, p["plan_id"])
    for w in store().where(WOS, "session_id", sid):
        store().delete(WOS, _wkey(sid, w["wo_id"]))
    for e in store().where(EVENTS, "session_id", sid):
        store().delete(EVENTS, f"{sid}/{e['event_id']}")


# --- capacity page --------------------------------------------------------------------------------------------------


def period_demand(sid: str, start: date, end: date) -> tuple[dict[str, int], dict[str, int]]:
    lo, hi = start.isoformat(), end.isoformat()
    demand: dict[str, int] = {}
    for h in order_view(sid)["orders"]:
        if h["status"] == "Cancelled":
            continue
        for ln in h["lines"]:
            if ln["status"] != "Cancelled" and lo <= ln["requested_delivery_date"] <= hi:
                demand[ln["sku"]] = demand.get(ln["sku"], 0) + int(ln["ordered_qty"])
    planned: dict[str, int] = {}
    for p in store().where(PLANS, "session_id", sid):
        if p["status"] != "Cancelled" and p["period_start"] <= hi and p["period_end"] >= lo:
            for ln in p["lines"]:
                planned[ln["sku"]] = planned.get(ln["sku"], 0) + int(ln["planned_qty"])
    return demand, planned


def capacity_page(sid: str, period_start: str | None, period_end: str | None, utilization: float | None = None) -> dict[str, Any]:
    if period_start is None and period_end is None:
        start, end = capacity.month_bounds(TODAY)
    else:
        start, end = capacity.parse_period(period_start, period_end)
    if utilization is not None:
        capacity.check_utilization(utilization)
    util = capacity.utilization_pct(sid) if utilization is None else float(utilization)
    o = effects.overrides(sid)
    demand, planned = period_demand(sid, start, end)
    rows = capacity.rows_for(demand, planned, start, end, util, o.maintenance, o.mold_moves)
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "planning_utilization_pct": util,
        "working_days": capacity.working_days(start, end),
        **capacity.page(rows),
    }


def assess_sku(sid: str, sku: str, qty: int, period_start: str | None, period_end: str | None) -> dict[str, Any]:
    if period_start is None and period_end is None:
        start, end = capacity.month_bounds(TODAY)
    else:
        start, end = capacity.parse_period(period_start, period_end)
    if sku not in plant.PRODUCT_BY_SKU:
        raise FVError(
            capacity.INVALID_ASSESSMENT, f"{sku} is not a product in the plant.", {"fields": [{"field": "sku", "message": "Use a SKU from the product list."}]}
        )
    if qty < 0:
        raise FVError(capacity.INVALID_ASSESSMENT, "The quantity is not valid.", {"fields": [{"field": "qty", "message": "Use a whole number of at least 0."}]})
    o = effects.overrides(sid)
    demand, _ = period_demand(sid, start, end)
    row = capacity.sku_capacity({**demand, sku: qty}, start, end, capacity.utilization_pct(sid), maintenance=o.maintenance, mold_moves=o.mold_moves)[sku]
    return {
        "sku": sku,
        "product_name": capacity.product_name(sku),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "qty": qty,
        "planning_utilization_pct": capacity.utilization_pct(sid),
        **row,
        "formulas": capacity.FORMULAS,
    }


def recalculate_capacity(
    sid: str, period_start: str | None, period_end: str | None, snapshot_id: str | None = None, utilization: float | None = None, user: str = "You"
) -> dict[str, Any]:
    if snapshot_id and period_start is None and period_end is None:
        old = capacity.get_snapshot(sid, snapshot_id)
        period_start, period_end = old["period_start"], old["period_end"]
        utilization = utilization if utilization is not None else float(old["planning_utilization_pct"])
    result = capacity_page(sid, period_start, period_end, utilization)
    keep = {k: result[k] for k in ("rows", "kpis", "formulas", "working_days")}
    doc = capacity.save_snapshot(sid, to_date(result["period_start"]), to_date(result["period_end"]), float(result["planning_utilization_pct"]), keep, user)
    for g in doc["gap_events"]:
        _event(sid, "CapacityGapDetected", {"plan_id": None, "plan_number": None}, {**g, "snapshot_id": doc["cap_id"], "period_start": doc["period_start"]})
    return doc

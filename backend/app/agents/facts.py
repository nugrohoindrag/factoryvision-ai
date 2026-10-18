from __future__ import annotations

from typing import Any

from app import actions, effects, forecasts, plans
from app.tools import planning
from app.tools.common import pct

Doc = dict[str, Any]

ARKA_GROUP_MACHINES = {"IMM-04", "IMM-05"}
SNAPSHOT_KEYS = (
    "forecast_id",
    "status",
    "superseded_by",
    "engine",
    "generated_at",
    "generated_by",
    "window_days",
    "horizon_weeks",
    "period_start",
    "period_end",
    "used_by_plans",
)
PLAN_KEYS = ("plan_id", "plan_number", "status", "period_start", "period_end", "forecast_id", "origin", "created_by", "utilization_pct")
STATUS_RANK = {"Within Plan": 0, "Additional Demand": 1, "Capacity Up Required": 2}


def _change(new: float, old: float | None) -> float | None:
    return pct(new / old - 1) if old else None


def sku_trend(snap: Doc, sku: str) -> Doc | None:
    lines = [ln for ln in snap.get("lines", []) if ln["sku"] == sku]
    if not lines:
        return None
    weeks: dict[str, int] = {}
    for ln in lines:
        weeks[ln["week_start"]] = weeks.get(ln["week_start"], 0) + int(ln["forecast_qty"])
    ordered = sorted(weeks.items())
    series = [{"week_start": w, "forecast_qty": q} for w, q in ordered]
    hist = (snap.get("history") or {}).get(sku) or []
    last = hist[-1] if hist else None
    head = lines[0]
    return {
        "weekly_forecast": series,
        "total_forecast_qty": sum(weeks.values()),
        "last_history_week": last,
        "change_final_week_vs_last_history_week_pct": _change(ordered[-1][1], float(last["qty"]) if last else None),
        "change_first_to_final_forecast_week_pct": _change(ordered[-1][1], ordered[0][1]),
        "history_weeks_with_orders": head.get("history_weeks_with_orders"),
        "history_weeks": head.get("history_weeks"),
        "insufficient_history": any(ln.get("insufficient_history") for ln in lines),
    }


def snapshot_view(snap: Doc, sku: str | None = None) -> Doc:
    skus = [sku] if sku else list(snap.get("skus") or sorted({ln["sku"] for ln in snap.get("lines", [])}))
    per = {s: t for s in skus if (t := sku_trend(snap, s)) is not None}
    flagged = sorted({ln["sku"] for ln in snap.get("lines", []) if ln.get("insufficient_history")})
    return {
        **{k: snap.get(k) for k in SNAPSHOT_KEYS},
        "skus": per,
        "insufficient_history_skus": flagged,
        "source": f"forecast snapshot {snap['forecast_id']} ({snap.get('engine')})",
    }


def worst_status(lines: list[Doc]) -> str | None:
    found = [str(ln.get("capacity", {}).get("status")) for ln in lines if ln.get("capacity", {}).get("status") in STATUS_RANK]
    return max(found, key=STATUS_RANK.__getitem__) if found else None


def plan_line_view(ln: Doc) -> Doc:
    cap = ln.get("capacity") or {}
    return {
        "sku": ln["sku"],
        "demand_qty": ln.get("demand_qty"),
        "forecast_qty": ln.get("forecast_qty"),
        "planned_qty": ln.get("planned_qty"),
        "priority": ln.get("priority"),
        "required_date": ln.get("required_date"),
        "capacity_status": cap.get("status"),
        "capacity": {k: cap.get(k) for k in ("total", "planning", "buffer", "gap", "utilization_pct")},
        "machines": [c.get("machine_id") for c in cap.get("contributions", [])],
        "uncomputed": cap.get("uncomputed", []),
    }


def plan_view(plan: Doc) -> Doc:
    lines = plan.get("lines", [])
    short = [ln["sku"] for ln in lines if (ln.get("capacity") or {}).get("status") == "Capacity Up Required"]
    return {
        **{k: plan.get(k) for k in PLAN_KEYS},
        "capacity_status": worst_status(lines),
        "capacity_up_required_skus": short,
        "lines": [plan_line_view(ln) for ln in lines],
        "readiness": plan.get("readiness"),
        "summary": plan.get("summary"),
        "work_orders": len(plan.get("work_orders", [])),
        "source": f"production plan {plan.get('plan_number')} (session state)",
    }


def current_plan(sid: str) -> Doc | None:
    return next((p for p in plans.list_plans(sid) if p.get("status") != "Cancelled"), None)


def _arka_group(cap: Doc) -> Doc:
    return next((g for g in cap["groups"] if set(g["machines"]) >= ARKA_GROUP_MACHINES), cap["groups"][0])


def molding_delta(o: effects.Overrides, before_change: dict[str, float] | None) -> Doc:
    cyc = {m: effects.normal_cycle_factor(planning.machine_rates()) for m in o.maintenance} or None
    extra = o.extra_shifts or None
    util = o.planning_utilization_pct
    before = planning.get_capacity(demand_change_pct=before_change or None, cycle_override=cyc, extra_days=extra, utilization_pct=util)
    after = planning.get_capacity(demand_change_pct=o.demand_change_pct or None, cycle_override=cyc, extra_days=extra, utilization_pct=util)
    b, a = _arka_group(before), _arka_group(after)
    return {
        "group": a["group"],
        "before_gap_pct": b["capacity_gap_pct"],
        "after_gap_pct": a["capacity_gap_pct"],
        "gap_change_pts": round(a["capacity_gap_pct"] - b["capacity_gap_pct"], 1),
        "before_status": b["status"],
        "after_status": a["status"],
        "bottleneck_machine": after["bottleneck_machine"],
        "bottleneck_load_pct": after["bottleneck_load_pct"],
        "source": after["source"],
    }


def replan_trigger(sid: str) -> Doc:
    o = effects.overrides(sid)
    fc = forecasts.latest(sid)
    plan = current_plan(sid)
    done = [a["command"]["args"] for a in actions.list_actions(sid, "approved") if a["kind"] == "replan"]
    last: Doc = done[-1] if done else {}
    planned_change: dict[str, float] = dict((plan or {}).get("demand_change_pct") or last.get("demand_change_pct") or {})
    reasons: list[str] = []
    fresh = fc["forecast_id"] not in {last.get("forecast_id"), (plan or {}).get("forecast_id")}
    if fresh and plan is None:
        reasons.append(f"No production plan uses the latest forecast snapshot {fc['forecast_id']} yet.")
    elif fresh and plan is not None:
        reasons.append(f"New forecast snapshot {fc['forecast_id']}; plan {plan['plan_number']} uses {plan.get('forecast_id')}.")
    changed = {k: v for k, v in o.demand_change_pct.items() if planned_change.get(k) != v}
    if changed:
        reasons.append("Revised customer PO changes demand: " + ", ".join(f"{k} {v:+g}%" for k, v in sorted(changed.items())) + ".")
    pending = next((a for a in actions.list_actions(sid, "pending") if a["kind"] == "replan"), None)
    return {
        "replan_needed": bool(reasons),
        "reasons": reasons,
        "latest_forecast_id": fc["forecast_id"],
        "current_plan": {k: plan.get(k) for k in ("plan_number", "status", "forecast_id")} if plan else None,
        "demand_change_pct": dict(o.demand_change_pct),
        "molding_capacity_delta": molding_delta(o, planned_change),
        "pending_replan_action_id": pending["action_id"] if pending else None,
        "source": f"forecast snapshot {fc['forecast_id']} + production plans + planning.get_capacity",
    }

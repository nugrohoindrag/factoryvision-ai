from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app import plant
from app.effects import normal_cycle_factor
from app.errors import FVError
from app.store import store
from app.tools import planning
from app.tools.common import AVAILABLE_FACTOR, MINUTES_PER_WORKDAY, TODAY, to_date

if TYPE_CHECKING:
    from collections.abc import Iterable

INVALID_PERIOD = "FV-CAP-001"
SNAPSHOT_NOT_FOUND = "FV-CAP-002"
INVALID_UTILIZATION = "FV-CAP-003"
INVALID_ASSESSMENT = "FV-CAP-004"

WITHIN, ADDITIONAL, UP = "Within Plan", "Additional Demand", "Capacity Up Required"
STATUS_RANK = {WITHIN: 0, ADDITIONAL: 1, UP: 2}
DEFAULT_UTILIZATION_PCT = 80.0
MAINTENANCE_DOWNTIME_MIN = 120  # the heater band and check ring job the maintenance action books (see planning.run_scheduler)
SNAPSHOTS = "capacity_snapshots"
SETTINGS = "planning_settings"

FORMULAS = {
    "total": "Total = sum over compatible machines of (effective minutes x share / cycle time); "
    "effective minutes = working days x 1,350 min x 0.88 - planned downtime",
    "planning": "Planning = floor(Total x planning utilization %)",
    "buffer": "Buffer = Total - Planning",
    "gap": "Gap = max(Demand - Total, 0)",
    "utilization_pct": "Utilization = Demand / Total x 100",
    "status": "Within Plan if Demand <= Planning; Additional Demand if Demand <= Total; otherwise Capacity Up Required",
    "share": "Machines of one tonnage class are shared by the SKUs whose mold sits in that class, in proportion to the minutes each SKU needs",
    "cycle": "Cycle time = ideal cycle x actual cycle factor (last 7 days) / yield; a machine in approved maintenance runs at the fleet's normal factor",
}


def status_of(demand: int, total: int, planning_capacity: int) -> str:
    if demand <= planning_capacity:
        return WITHIN
    if demand <= total:
        return ADDITIONAL
    return UP


def worst(statuses: Iterable[str | None]) -> str | None:
    known = [s for s in statuses if s in STATUS_RANK]
    return max(known, key=STATUS_RANK.__getitem__) if known else None


def assess(machines: list[dict[str, Any]], utilization_pct: float, demand: int) -> dict[str, Any]:
    contributions: list[dict[str, Any]] = []
    uncomputed: list[dict[str, Any]] = []
    total = 0
    for m in machines:
        if m.get("reason"):
            uncomputed.append({"machine_id": m["machine_id"], "reason": m["reason"]})
            continue
        cycle = m.get("cycle_s")
        if not cycle or cycle <= 0:
            uncomputed.append(
                {
                    "machine_id": m["machine_id"],
                    "reason": f"{m['machine_id']} has no measured cycle time for this product, so its capacity is not computed; that does not mean it is zero.",
                }
            )
            continue
        effective = max(float(m["available_minutes"]) - float(m.get("downtime_minutes", 0.0)), 0.0) * float(m.get("share", 1.0))
        cap = math.floor(effective * 60 / cycle)
        total += cap
        contributions.append(
            {
                "machine_id": m["machine_id"],
                "available_minutes": round(effective, 1),
                "downtime_minutes": round(float(m.get("downtime_minutes", 0.0)), 1),
                "share_pct": round(100 * float(m.get("share", 1.0)), 1),
                "cycle_s": round(cycle, 2),
                "capacity": cap,
            }
        )
    plan_cap = math.floor(total * utilization_pct / 100)
    return {
        "demand": demand,
        "total": total,
        "planning": plan_cap,
        "buffer": total - plan_cap,
        "gap": max(demand - total, 0),
        "utilization_pct": round(100 * demand / total, 1) if total else 0.0,
        "planning_utilization_pct": utilization_pct,
        "status": status_of(demand, total, plan_cap),
        "contributions": contributions,
        "uncomputed": uncomputed,
    }


def working_days(start: date, end: date) -> int:
    return sum(1 for k in range((end - start).days + 1) if plant.is_working_day(start + timedelta(days=k)))


def period_minutes(start: date, end: date) -> float:
    return working_days(start, end) * MINUTES_PER_WORKDAY * AVAILABLE_FACTOR


def planned_downtime(start: date, end: date, maintenance: Iterable[str]) -> dict[str, float]:
    return {mid: float(MAINTENANCE_DOWNTIME_MIN) for mid in maintenance if start <= TODAY <= end}


def cycle_seconds(rates: dict[str, dict[str, Any]], maintenance: Iterable[str]) -> dict[tuple[str, str], float]:
    normal = normal_cycle_factor(rates)
    down = set(maintenance)
    out: dict[tuple[str, str], float] = {}
    for sku, mids in plant.MOLD_COMPAT.items():
        for mid in mids:
            r = rates.get(mid)
            if r and r.get("yield"):
                factor = normal if mid in down else float(r["cycle_factor"])
                out[sku, mid] = plant.PRODUCT_BY_SKU[sku].ideal_cycle_s * factor / float(r["yield"])
    return out


def homes(mold_moves: dict[str, str] | None = None) -> dict[str, str]:
    return {**planning.current_molds(), **(mold_moves or {})}


def _tonnage(mid: str) -> int:
    return int(plant.MACHINE_BY_ID[mid].tonnage or 0)


def sku_capacity(
    demand: dict[str, int],
    start: date,
    end: date,
    utilization_pct: float,
    *,
    maintenance: Iterable[str] = (),
    mold_moves: dict[str, str] | None = None,
    rates: dict[str, dict[str, Any]] | None = None,
    home: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    maintenance = list(maintenance)
    rates = planning.machine_rates() if rates is None else rates
    home = home or homes(mold_moves)
    cycles = cycle_seconds(rates, maintenance)
    minutes = period_minutes(start, end)
    down = planned_downtime(start, end, maintenance)

    def need(sku: str) -> float:
        c = cycles.get((sku, home[sku]), plant.PRODUCT_BY_SKU[sku].ideal_cycle_s)
        return max(demand[sku], 0) * c / 60

    out: dict[str, dict[str, Any]] = {}
    for sku, qty in demand.items():
        cls = _tonnage(home[sku])
        peers = [s for s in demand if _tonnage(home[s]) == cls]
        pool = sum(need(s) for s in peers)
        share = need(sku) / pool if pool > 0 else 1 / len(peers)
        machines: list[dict[str, Any]] = []
        for mid in plant.MOLD_COMPAT[sku]:
            if _tonnage(mid) != cls:
                mounted = sorted(s for s, h in home.items() if _tonnage(h) == _tonnage(mid))
                machines.append(
                    {
                        "machine_id": mid,
                        "reason": f"{mid} is a {_tonnage(mid):,} t machine that runs the molds mounted in its class ({', '.join(mounted)}); "
                        f"it fits {sku} but is not counted for it.",
                    }
                )
                continue
            machines.append(
                {"machine_id": mid, "available_minutes": minutes, "downtime_minutes": down.get(mid, 0.0), "cycle_s": cycles.get((sku, mid)), "share": share}
            )
        out[sku] = assess(machines, utilization_pct, int(qty))
    return out


def utilization_pct(sid: str) -> float:
    doc = store().get(SETTINGS, sid)
    return float(doc["planning_utilization_pct"]) if doc else DEFAULT_UTILIZATION_PCT


def set_utilization_pct(sid: str, value: float) -> dict[str, Any]:
    check_utilization(value)
    store().set(SETTINGS, sid, {"session_id": sid, "planning_utilization_pct": float(value)})
    return settings(sid)


def check_utilization(value: float) -> None:
    if not 0 < value <= 100:
        raise FVError(
            INVALID_UTILIZATION,
            f"Planning utilization {value:g}% is not valid.",
            {"fields": [{"field": "planning_utilization_pct", "message": "Use a percentage above 0 and at most 100."}]},
        )


def settings(sid: str) -> dict[str, Any]:
    return {"planning_utilization_pct": utilization_pct(sid), "default_planning_utilization_pct": DEFAULT_UTILIZATION_PCT}


def parse_period(start: str | None, end: str | None, code: str = INVALID_PERIOD) -> tuple[date, date]:
    fields: list[dict[str, str]] = []
    parsed: dict[str, date] = {}
    for name, raw in (("period_start", start), ("period_end", end)):
        try:
            parsed[name] = to_date(raw) if raw else date.min
        except ValueError:
            fields.append({"field": name, "message": "Not a date."})
            continue
        if not raw:
            fields.append({"field": name, "message": "Required."})
    if not fields and parsed["period_end"] < parsed["period_start"]:
        fields.append({"field": "period_end", "message": "Period end must be on or after period start."})
    if fields:
        raise FVError(code, "The period is not valid.", {"fields": fields})
    return parsed["period_start"], parsed["period_end"]


def month_bounds(d: date) -> tuple[date, date]:
    first = d.replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(r["total"] for r in rows)
    demand = sum(r["demand_qty"] for r in rows)
    kpis = {
        "demand": demand,
        "total": total,
        "planning": sum(r["planning"] for r in rows),
        "buffer": sum(r["buffer"] for r in rows),
        "gap": sum(r["gap"] for r in rows),
        "utilization_pct": round(100 * demand / total, 1) if total else 0.0,
        "status": worst(r["status"] for r in rows),
    }
    return {"rows": rows, "kpis": kpis, "formulas": FORMULAS}


def rows_for(
    demand: dict[str, int], planned: dict[str, int], start: date, end: date, utilization: float, maintenance: Iterable[str], mold_moves: dict[str, str]
) -> list[dict[str, Any]]:
    caps = sku_capacity(demand, start, end, utilization, maintenance=maintenance, mold_moves=mold_moves)
    return [
        {"sku": sku, "product_name": product_name(sku), "demand_qty": demand[sku], "planned_qty": planned.get(sku, 0), **caps[sku]}
        for sku in sorted(demand)
        if demand[sku] > 0
    ]


def product_name(sku: str) -> str:
    p = plant.PRODUCT_BY_SKU[sku]
    return f"{p.position} bumper {p.model} ({p.segment})"


def _skey(sid: str, cap_id: str) -> str:
    return f"{sid}/{cap_id}"


def snapshots(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(SNAPSHOTS, "session_id", sid), key=lambda d: (str(d["computed_at"]), d["cap_id"]), reverse=True)


def get_snapshot(sid: str, cap_id: str) -> dict[str, Any]:
    doc = store().get(SNAPSHOTS, _skey(sid, cap_id))
    if doc is None:
        raise FVError(SNAPSHOT_NOT_FOUND, f"Capacity snapshot {cap_id} does not exist in this session.")
    return doc


def save_snapshot(sid: str, start: date, end: date, utilization: float, result: dict[str, Any], user: str) -> dict[str, Any]:
    prefix = f"CAP-{start:%Y-%m}"
    mine = snapshots(sid)
    n = max((int(d["cap_id"].rsplit("-", 1)[1]) for d in mine if d["cap_id"].startswith(prefix + "-")), default=0) + 1
    cap_id = f"{prefix}-{n:03d}"
    superseded = [d for d in mine if d["status"] == "Computed" and d["period_start"] == start.isoformat() and d["period_end"] == end.isoformat()]
    for old in superseded:
        store().update(SNAPSHOTS, _skey(sid, old["cap_id"]), {"status": "Superseded", "superseded_by": cap_id})
    doc = {
        "cap_id": cap_id,
        "snapshot_id": cap_id,
        "session_id": sid,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "planning_utilization_pct": utilization,
        "status": "Computed",
        "superseded_by": None,
        "supersedes": [d["cap_id"] for d in superseded],
        "computed_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "computed_by": user,
        **result,
        "gap_events": [
            {"event_type": "CapacityGapDetected", "sku": r["sku"], "demand": r["demand_qty"], "total": r["total"], "gap": r["gap"], "status": r["status"]}
            for r in result["rows"]
            if r["status"] == UP
        ],
    }
    store().set(SNAPSHOTS, _skey(sid, cap_id), doc)
    return doc


def clear(sid: str) -> None:
    for d in store().where(SNAPSHOTS, "session_id", sid):
        store().delete(SNAPSHOTS, _skey(sid, d["cap_id"]))
    store().delete(SETTINGS, sid)

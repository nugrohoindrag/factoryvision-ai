from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any

from ortools.sat.python import cp_model

from app import plant, warehouse
from app.errors import FVError
from app.tools.common import AVAILABLE_FACTOR, MINUTES_PER_WORKDAY, TODAY, pct, to_date, working_days_in_month

CYCLE_SQL = """
SELECT machine_id, SUM(actual_cycle_s * total_quantity) / SUM(total_quantity) AS cycle_s,
  SUM(output_quantity) / SUM(total_quantity) AS yield_ratio, MAX(ideal_cycle_s) AS ideal
FROM fv.fact_production
WHERE machine_id LIKE 'IMM-%' AND production_date > :d0
GROUP BY machine_id
"""

HEATER_BAND_AND_RING_REPAIR_IDR = 18_000_000


def _month(month: str | None) -> date:
    return to_date(month).replace(day=1) if month else TODAY.replace(day=1)


def machine_rates() -> dict[str, dict[str, Any]]:
    rows = warehouse.query(CYCLE_SQL, {"d0": TODAY - timedelta(days=8)})
    return {r["machine_id"]: {"cycle_factor": r["cycle_s"] / r["ideal"], "yield": r["yield_ratio"], "cycle_s": r["cycle_s"]} for r in rows}


def monthly_plan(month: date, demand_change_pct: dict[str, float] | None = None) -> dict[str, int]:
    rows = warehouse.query("SELECT sku, plan_qty FROM fv.fact_monthly_plan WHERE plan_month = :m", {"m": month})
    plan = {r["sku"]: int(r["plan_qty"]) for r in rows}
    for sku, chg in (demand_change_pct or {}).items():
        if sku in plan:
            plan[sku] = round(plan[sku] * (1 + float(chg) / 100))
    return plan


def current_molds() -> dict[str, str]:
    return {r["sku"]: r["current_machine_id"] for r in warehouse.query("SELECT sku, current_machine_id FROM fv.dim_mold")}


def current_molds_by_machine() -> dict[str, str]:
    return {mid: sku for sku, mid in current_molds().items()}


def group_status(required: float, available: float, utilization_pct: float) -> str:
    if required > available:
        return "Capacity Up Required"
    if required > available * utilization_pct / 100:
        return "Additional Demand"
    return "Within Plan"


def get_capacity(
    month: str | None = None,
    demand_change_pct: dict[str, float] | None = None,
    cycle_override: dict[str, float] | None = None,
    extra_days: dict[str, int] | None = None,
    utilization_pct: float = 80.0,
) -> dict[str, Any]:
    m = _month(month)
    plan = monthly_plan(m, demand_change_pct)
    rates = machine_rates()
    for mid, f in (cycle_override or {}).items():
        rates[mid] = {**rates[mid], "cycle_factor": f}
    molds = current_molds()
    wd = working_days_in_month(m)
    avail = {mid: (wd + (extra_days or {}).get(mid, 0)) * MINUTES_PER_WORKDAY * AVAILABLE_FACTOR for mid in plant.IMM_IDS}
    need = dict.fromkeys(plant.IMM_IDS, 0.0)
    for sku, qty in plan.items():
        mid = molds[sku]
        r = rates[mid]
        need[mid] += qty * plant.PRODUCT_BY_SKU[sku].ideal_cycle_s * r["cycle_factor"] / 60 / r["yield"]
    machines: list[dict[str, Any]] = [
        {
            "machine_id": mid,
            "skus": [s for s, x in molds.items() if x == mid],
            "required_minutes": round(need[mid]),
            "available_minutes": round(avail[mid]),
            "load_pct": pct(need[mid] / avail[mid]),
            "cycle_factor": round(rates[mid]["cycle_factor"], 3),
        }
        for mid in plant.IMM_IDS
    ]
    groups: list[dict[str, Any]] = []
    for name, mids in (
        ("3500t (IMM-04, IMM-05; only machines that fit the ARKA molds)", ["IMM-04", "IMM-05"]),
        ("other IMMs", [x for x in plant.IMM_IDS if x not in ("IMM-04", "IMM-05")]),
    ):
        req, av = sum(need[x] for x in mids), sum(avail[x] for x in mids)
        groups.append(
            {
                "group": name,
                "machines": mids,
                "required_minutes": round(req),
                "available_minutes": round(av),
                "capacity_gap_pct": pct(req / av - 1),
                "status": group_status(req, av, utilization_pct),
            }
        )
    bottleneck = max(machines, key=lambda x: x["load_pct"])
    return {
        "month": m.isoformat(),
        "working_days": wd,
        "plan_qty": plan,
        "demand_change_pct": demand_change_pct or {},
        "planning_utilization_pct": utilization_pct,
        "machines": machines,
        "groups": groups,
        "bottleneck_machine": bottleneck["machine_id"],
        "bottleneck_load_pct": bottleneck["load_pct"],
        "assumptions": {
            "minutes_per_workday": MINUTES_PER_WORKDAY,
            "available_factor": AVAILABLE_FACTOR,
            "cycle": "actual last 7 days",
            "yield": "output/total last 7 days",
        },
        "source": "fv.fact_monthly_plan, fv.fact_production, fv.dim_mold",
    }


def run_scheduler(
    month: str | None = None,
    demand_change_pct: dict[str, float] | None = None,
    *,
    allow_mold_moves: bool = True,
    maintenance: list[str] | None = None,
    extra_shifts: dict[str, int] | None = None,
    weeks: int = 4,
    time_limit_s: float = 10.0,
) -> dict[str, Any]:
    t0 = time.time()
    m = _month(month)
    plan = monthly_plan(m, demand_change_pct)
    rates = machine_rates()
    molds = current_molds()
    maintenance = maintenance or []
    wd = working_days_in_month(m)
    week_days = [wd // weeks + (1 if k < wd % weeks else 0) for k in range(weeks)]
    normal_factor = sum(float(rates[x]["cycle_factor"]) for x in plant.IMM_IDS if x != "IMM-04") / (len(plant.IMM_IDS) - 1)

    def factor(mid: str, _w: int) -> float:
        if mid in maintenance:
            return normal_factor
        return float(rates[mid]["cycle_factor"])

    model = cp_model.CpModel()
    scale = 10
    x: dict[tuple[str, str, int], cp_model.IntVar] = {}
    on: dict[tuple[str, str, int], cp_model.IntVar] = {}
    for sku in plan:
        for mid in plant.MOLD_COMPAT[sku]:
            for w in range(weeks):
                x[sku, mid, w] = model.new_int_var(0, plan[sku], f"x_{sku}_{mid}_{w}")
                on[sku, mid, w] = model.new_bool_var(f"on_{sku}_{mid}_{w}")
                model.add(x[sku, mid, w] == 0).only_enforce_if(on[sku, mid, w].negated())
                if not allow_mold_moves and mid != molds[sku]:
                    model.add(on[sku, mid, w] == 0)
        for w in range(weeks):
            model.add(sum(on[sku, mid, w] for mid in plant.MOLD_COMPAT[sku]) <= 1)
    unmet = {sku: model.new_int_var(0, plan[sku], f"unmet_{sku}") for sku in plan}
    for sku in plan:
        model.add(sum(x[sku, mid, w] for mid in plant.MOLD_COMPAT[sku] for w in range(weeks)) + unmet[sku] == plan[sku])
    changeovers: list[cp_model.IntVar] = []
    moves: list[cp_model.IntVar] = []
    for mid in plant.IMM_IDS:
        for w in range(weeks):
            minutes = (week_days[w] + (extra_shifts or {}).get(mid, 0) / weeks) * MINUTES_PER_WORKDAY * AVAILABLE_FACTOR
            if mid in maintenance and w == 0:
                minutes -= 120
            cap = int(minutes * 60 / scale)
            here = [sku for sku in plan if mid in plant.MOLD_COMPAT[sku]]
            load = []
            for sku in here:
                sec = plant.PRODUCT_BY_SKU[sku].ideal_cycle_s * factor(mid, w) / rates[mid]["yield"]
                load.append(x[sku, mid, w] * round(sec * 100 / scale))
            n_on = sum(on[sku, mid, w] for sku in here)
            extra = model.new_int_var(0, len(here), f"co_{mid}_{w}")
            model.add(extra >= n_on - 1)
            changeovers.append(extra)
            model.add(sum(load) + extra * int(plant.CHANGEOVER_MINUTES * 60 * 100 / scale) <= cap * 100)
            moves.extend(on[sku, mid, w] for sku in here if mid != molds[sku])
    model.minimize(1000 * sum(unmet.values()) + 50 * sum(changeovers) + 20 * sum(moves))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return FVError("FV-PLAN-001", detail={"solver_status": solver.status_name(status)}).tool_result()
    schedule: list[dict[str, Any]] = []
    for (sku, mid, w), var in x.items():
        q = solver.value(var)
        if q:
            schedule.append({"week": w + 1, "machine_id": mid, "sku": sku, "qty": q, "mold_moved": mid != molds[sku]})
    schedule.sort(key=lambda r: (r["week"], r["machine_id"]))
    unmet_v = {sku: solver.value(v) for sku, v in unmet.items()}
    total_plan = sum(plan.values())
    return {
        "status": solver.status_name(status),
        "solve_seconds": round(time.time() - t0, 2),
        "month": m.isoformat(),
        "plan_qty": plan,
        "unmet_qty": unmet_v,
        "fill_rate_pct": pct(1 - sum(unmet_v.values()) / total_plan),
        "unmet_pct_by_sku": {s: pct(u / plan[s]) for s, u in unmet_v.items() if u},
        "changeovers": int(sum(solver.value(c) for c in changeovers)),
        "mold_moves": sorted({f"{r['sku']} -> {r['machine_id']} (week {r['week']})" for r in schedule if r["mold_moved"]}),
        "options": {"allow_mold_moves": allow_mold_moves, "maintenance": maintenance, "extra_shifts": extra_shifts or {}},
        "schedule": schedule,
        "engine": "OR-Tools CP-SAT",
        "source": "fv.fact_monthly_plan, fv.fact_production, fv.product_mold_compatibility",
    }


WHAT_IF: dict[str, dict[str, Any]] = {
    "baseline": {},
    "maintenance_imm04": {"maintenance": ["IMM-04"]},
    "move_arka_mold_imm05": {"allow_mold_moves": True},
    "sunday_shifts_3500t": {"extra_shifts": {"IMM-04": 4, "IMM-05": 4}},
    "maintenance_plus_sunday": {"maintenance": ["IMM-04"], "extra_shifts": {"IMM-04": 4, "IMM-05": 4}},
}


def what_if(scenarios: list[str] | None = None, demand_change_pct: dict[str, float] | None = None) -> dict[str, Any]:
    scenarios = scenarios or list(WHAT_IF)
    out: list[dict[str, Any]] = []
    for name in scenarios:
        opts: dict[str, Any] = {"allow_mold_moves": False, **WHAT_IF.get(name, {})}
        r = run_scheduler(demand_change_pct=demand_change_pct, time_limit_s=5.0, **opts)
        out.append(
            {
                "scenario": name,
                "fill_rate_pct": r.get("fill_rate_pct"),
                "unmet_qty": {k: v for k, v in r.get("unmet_qty", {}).items() if v},
                "changeovers": r.get("changeovers"),
                "mold_moves": r.get("mold_moves"),
                "extra_cost_idr": _scenario_cost(opts),
            }
        )
    return {"demand_change_pct": demand_change_pct or {}, "scenarios": out, "engine": "OR-Tools CP-SAT"}


def _scenario_cost(opts: dict[str, Any]) -> int:
    cost = 0
    for _mid in opts.get("maintenance", []):
        cost += 2 * plant.DOWNTIME_IDR_PER_HOUR_IMM + HEATER_BAND_AND_RING_REPAIR_IDR
    cost += sum(opts.get("extra_shifts", {}).values()) * 3 * plant.OVERTIME_IDR_PER_SHIFT
    return int(cost)


PAINT_LINE = "PNT-01"  # primer booth heads the PNT-01 -> PNT-02 -> OVN-01 conveyor, so the line is one resource
PAINT_TAKT_S = 18.0
ASSEMBLY_CELLS = ("ASM-01", "ASM-02", "ASM-03")
ASSEMBLY_TAKT_S = 55.0
FLOW_LAG_MIN = 60  # the first racks reach the next process an hour after it starts
SHIFT_PLANNED_MIN = 450
SHIFT_SPAN_MIN = 480
SHIFT_A_START_HOUR = 7


def clock(day: date, minute: int) -> datetime:
    shift, offset = divmod(minute, SHIFT_PLANNED_MIN)
    return datetime(day.year, day.month, day.day, SHIFT_A_START_HOUR, tzinfo=plant.WIB) + timedelta(minutes=shift * SHIFT_SPAN_MIN + offset)


def _timeline_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if plant.is_working_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _duration(qty: int, takt_s: float) -> int:
    return max(1, ceil(qty * takt_s / 60 / AVAILABLE_FACTOR))


def schedule_work_orders(
    jobs: list[dict[str, Any]],
    start: date,
    cycle_s: dict[tuple[str, str], float],
    home: dict[str, str],
    *,
    blocked: dict[str, int] | None = None,
    time_limit_s: float = 10.0,
) -> dict[str, Any]:
    t0 = time.time()
    blocked = blocked or {}
    model = cp_model.CpModel()
    options: dict[str, list[tuple[str, int]]] = {}
    for j in jobs:
        if j["process"] == "Molding":
            opts = []
            for mid in plant.MOLD_COMPAT[j["sku"]]:
                if (j["sku"], mid) in cycle_s:
                    setup = 0 if home.get(j["sku"]) == mid else plant.CHANGEOVER_MINUTES
                    opts.append((mid, _duration(j["qty"], cycle_s[j["sku"], mid]) + setup))
            options[j["wo_id"]] = opts
        elif j["process"] == "Painting":
            options[j["wo_id"]] = [(PAINT_LINE, _duration(j["qty"], PAINT_TAKT_S))]
        else:
            options[j["wo_id"]] = [(c, _duration(j["qty"], ASSEMBLY_TAKT_S)) for c in ASSEMBLY_CELLS]
    if any(not o for o in options.values()):
        return FVError(
            "FV-PP-028", "A molding work order has no compatible machine with a cycle time.", {"work_orders": [k for k, o in options.items() if not o]}
        ).tool_result()
    horizon = sum(max(d for _, d in o) for o in options.values()) + max(blocked.values(), default=0) + 2 * FLOW_LAG_MIN * len(jobs) + MINUTES_PER_WORKDAY
    days = _timeline_days(start, horizon // MINUTES_PER_WORKDAY + 2)
    s_var: dict[str, cp_model.IntVar] = {}
    e_var: dict[str, cp_model.IntVar] = {}
    pick: dict[tuple[str, str], cp_model.IntVar] = {}
    per_res: dict[str, list[cp_model.IntervalVar]] = {}
    for mid, minutes in blocked.items():
        per_res.setdefault(mid, []).append(model.new_fixed_size_interval_var(0, minutes, f"blocked_{mid}"))
    moves = []
    for j in jobs:
        w = j["wo_id"]
        s_var[w] = model.new_int_var(0, horizon, f"s_{w}")
        e_var[w] = model.new_int_var(0, horizon, f"e_{w}")
        lits = []
        for mid, dur in options[w]:
            lit = model.new_bool_var(f"on_{w}_{mid}")
            per_res.setdefault(mid, []).append(model.new_optional_interval_var(s_var[w], dur, e_var[w], lit, f"iv_{w}_{mid}"))
            pick[w, mid] = lit
            lits.append(lit)
            if j["process"] == "Molding" and home.get(j["sku"]) != mid:
                moves.append(lit)
        model.add_exactly_one(lits)
    for ivs in per_res.values():
        model.add_no_overlap(ivs)
    by_id = {j["wo_id"]: j for j in jobs}
    for j in jobs:
        p = j.get("predecessor_wo_id")
        if isinstance(p, str) and p in by_id:
            model.add(s_var[j["wo_id"]] >= s_var[p] + FLOW_LAG_MIN)
            model.add(e_var[j["wo_id"]] >= e_var[p] + FLOW_LAG_MIN // 2)
    successors = {j.get("predecessor_wo_id") for j in jobs}
    tardy = []
    for j in jobs:
        if j["wo_id"] in successors:
            continue
        due_idx = sum(1 for d in days if d <= j["due"])
        late = model.new_int_var(0, horizon, f"late_{j['wo_id']}")
        model.add(late >= e_var[j["wo_id"]] - due_idx * MINUTES_PER_WORKDAY)
        tardy.append(late * (6 - int(j.get("priority", 3))))
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(e_var.values()))
    model.minimize(10 * sum(tardy) + 500 * sum(moves) + makespan)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return FVError("FV-PP-028", detail={"solver_status": solver.status_name(status)}).tool_result()
    out: list[dict[str, Any]] = []
    for j in jobs:
        w = j["wo_id"]
        mid = next(m for m, _ in options[w] if solver.value(pick[w, m]))
        s, e = solver.value(s_var[w]), solver.value(e_var[w])
        d0, m0 = divmod(s, MINUTES_PER_WORKDAY)
        d1, m1 = divmod(e, MINUTES_PER_WORKDAY)
        begin, finish = clock(days[d0], m0), clock(days[d1], m1)
        due_end = sum(1 for d in days if d <= j["due"]) * MINUTES_PER_WORKDAY
        out.append(
            {
                "wo_id": w,
                "machine_id": mid,
                "mold_id": plant.MOLD_ID[j["sku"]] if j["process"] == "Molding" else None,
                "start_minute": s,
                "end_minute": e,
                "planned_start": begin.isoformat(),
                "planned_end": finish.isoformat(),
                "shift": plant.shift_of(begin),
                "mold_moved": j["process"] == "Molding" and home.get(j["sku"]) != mid,
                "late_minutes": max(0, e - due_end) if w not in successors else 0,
            }
        )
    for mid in {r["machine_id"] for r in out}:
        for k, r in enumerate(sorted((r for r in out if r["machine_id"] == mid), key=lambda r: r["start_minute"]), 1):
            r["sequence"] = k
    return {
        "status": solver.status_name(status),
        "solve_seconds": round(time.time() - t0, 2),
        "engine": "OR-Tools CP-SAT",
        "assignments": out,
        "late_work_orders": [r["wo_id"] for r in out if r["late_minutes"] > 0],
        "assumptions": {
            "timeline": f"{MINUTES_PER_WORKDAY} planned minutes per working day (3 shifts x {SHIFT_PLANNED_MIN}), Sundays off",
            "molding": "qty x cycle time / 0.88 availability, + 90 min changeover when the mold moves off its mounted machine",
            "painting": f"one line {PAINT_LINE}->PNT-02->OVN-01 at {PAINT_TAKT_S:g} s/unit",
            "assembly": f"{len(ASSEMBLY_CELLS)} cells at {ASSEMBLY_TAKT_S:g} s/unit",
            "flow": f"a process starts {FLOW_LAG_MIN} min after its predecessor starts and ends {FLOW_LAG_MIN // 2} min after it ends",
        },
    }

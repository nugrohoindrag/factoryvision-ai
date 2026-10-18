from __future__ import annotations

import time
from datetime import date, timedelta
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


def get_capacity(
    month: str | None = None,
    demand_change_pct: dict[str, float] | None = None,
    cycle_override: dict[str, float] | None = None,
    extra_days: dict[str, int] | None = None,
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
                "status": "Capacity Up Required" if req > av else ("Additional Demand" if req > 0.95 * av else "Within Plan"),
            }
        )
    bottleneck = max(machines, key=lambda x: x["load_pct"])
    return {
        "month": m.isoformat(),
        "working_days": wd,
        "plan_qty": plan,
        "demand_change_pct": demand_change_pct or {},
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

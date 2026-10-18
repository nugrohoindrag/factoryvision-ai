from datetime import date
from itertools import pairwise
from typing import Any

import pytest
from app import capacity, plans, plant
from app.errors import FVError
from app.tools import planning

RATES = {m: {"cycle_factor": 1.0, "yield": 1.0} for m in ("IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06")}
HOME = {
    "FB-CITRA": "IMM-01",
    "RB-CITRA": "IMM-02",
    "FB-BROMO": "IMM-03",
    "FB-ARKA": "IMM-04",
    "RB-ARKA": "IMM-05",
    "RB-BROMO": "IMM-06",
}


def _plan(lines: list[dict[str, Any]] | None = None, demand: list[dict[str, Any]] | None = None, step: int = 1, status: str = "Planning") -> dict[str, Any]:
    return {"plan_id": "p", "plan_number": "PP-2027-02", "status": status, "lines": lines or [], "demand": demand or [], "wizard_step": step}


def _line(planned: int = 100, demand: int = 100, forecast: int = 0, cap: str | None = capacity.WITHIN) -> dict[str, Any]:
    return {
        "line_id": "L1",
        "sku": "FB-ARKA",
        "demand_qty": demand,
        "forecast_qty": forecast,
        "planned_qty": planned,
        "priority": 1,
        "capacity": {"status": cap} if cap else None,
    }


def _wo(wo_id: str = "W1", **kw: Any) -> dict[str, Any]:
    base = {
        "wo_id": wo_id,
        "process": "Molding",
        "sku": "FB-ARKA",
        "qty": 100,
        "machine_id": "IMM-04",
        "mold_id": "MLD-FB-ARKA",
        "planned_start": "2027-02-01T07:00:00+07:00",
        "planned_end": "2027-02-01T09:00:00+07:00",
        "shift": "A",
        "confirmed": False,
        "status": "Draft",
    }
    return {**base, **kw}


# --- capacity math (PP-R43, PP-R44) ---


def test_status_thresholds() -> None:
    assert capacity.status_of(80, 100, 80) == capacity.WITHIN
    assert capacity.status_of(81, 100, 80) == capacity.ADDITIONAL
    assert capacity.status_of(100, 100, 80) == capacity.ADDITIONAL
    assert capacity.status_of(101, 100, 80) == capacity.UP


def test_worst_status_ignores_unknown() -> None:
    assert capacity.worst([capacity.WITHIN, None, capacity.UP, capacity.ADDITIONAL]) == capacity.UP
    assert capacity.worst([None]) is None


def test_assess_formula_and_downtime() -> None:
    r = capacity.assess(
        [
            {"machine_id": "A", "available_minutes": 1000, "downtime_minutes": 100, "cycle_s": 60},
            {"machine_id": "B", "available_minutes": 50, "downtime_minutes": 80, "cycle_s": 30},
        ],
        80,
        950,
    )
    assert r["total"] == 900
    assert r["planning"] == 720
    assert r["buffer"] == 180
    assert r["gap"] == 50
    assert r["utilization_pct"] == pytest.approx(105.6)
    assert r["status"] == capacity.UP
    assert [c["capacity"] for c in r["contributions"]] == [900, 0]


def test_machine_without_cycle_is_uncomputed_not_zero() -> None:
    r = capacity.assess([{"machine_id": "A", "available_minutes": 100, "cycle_s": None}, {"machine_id": "B", "reason": "inactive"}], 80, 10)
    assert r["total"] == 0
    assert [u["machine_id"] for u in r["uncomputed"]] == ["A", "B"]
    assert "not mean it is zero" in r["uncomputed"][0]["reason"]
    assert r["utilization_pct"] == 0.0


def test_working_days_skip_sunday() -> None:
    assert capacity.working_days(date(2027, 2, 1), date(2027, 2, 28)) == 24
    assert capacity.working_days(date(2027, 2, 7), date(2027, 2, 7)) == 0


def test_sku_capacity_counts_only_compatible_machines_in_class() -> None:
    caps = capacity.sku_capacity({"FB-BROMO": 100}, date(2027, 2, 1), date(2027, 2, 28), 80, rates=RATES, home=HOME)
    r = caps["FB-BROMO"]
    assert sorted(c["machine_id"] for c in r["contributions"]) == ["IMM-03", "IMM-06"]
    assert sorted(u["machine_id"] for u in r["uncomputed"]) == ["IMM-04", "IMM-05"]
    minutes = 24 * 1350 * 0.88
    per = int(minutes * 60 / plant.PRODUCT_BY_SKU["FB-BROMO"].ideal_cycle_s)
    assert r["total"] == pytest.approx(2 * per, abs=2)


def test_sku_capacity_shares_a_class_by_need() -> None:
    caps = capacity.sku_capacity({"FB-ARKA": 3000, "RB-ARKA": 1000}, date(2027, 2, 1), date(2027, 2, 28), 80, rates=RATES, home=HOME)
    a, b = caps["FB-ARKA"]["contributions"][0]["share_pct"], caps["RB-ARKA"]["contributions"][0]["share_pct"]
    assert a + b == pytest.approx(100, abs=0.2)
    assert a > b


def test_maintenance_subtracts_planned_downtime_and_uses_normal_cycle() -> None:
    rates = {**RATES, "IMM-04": {"cycle_factor": 1.5, "yield": 1.0}}
    slow = capacity.sku_capacity({"FB-ARKA": 10}, date(2027, 2, 1), date(2027, 2, 28), 80, rates=rates, home=HOME)
    fixed = capacity.sku_capacity({"FB-ARKA": 10}, date(2027, 2, 1), date(2027, 2, 28), 80, rates=rates, home=HOME, maintenance=["IMM-04"])
    c = next(x for x in fixed["FB-ARKA"]["contributions"] if x["machine_id"] == "IMM-04")
    assert c["downtime_minutes"] == capacity.MAINTENANCE_DOWNTIME_MIN
    assert fixed["FB-ARKA"]["total"] > slow["FB-ARKA"]["total"]


def test_utilization_must_be_a_percentage() -> None:
    for bad in (0, -5, 101):
        with pytest.raises(FVError) as e:
            capacity.check_utilization(bad)
        assert e.value.code == "FV-CAP-003"
    capacity.check_utilization(100)


def test_period_validation() -> None:
    assert capacity.parse_period("2027-02-01", "2027-02-01") == (date(2027, 2, 1), date(2027, 2, 1))
    with pytest.raises(FVError) as e:
        capacity.parse_period("2027-02-10", "2027-02-01", plans.INVALID_PERIOD)
    assert e.value.code == "FV-PP-002"
    with pytest.raises(FVError) as e:
        capacity.parse_period(None, "bad")
    detail: Any = e.value.detail
    assert {f["field"] for f in detail["fields"]} == {"period_start", "period_end"}


def test_month_bounds() -> None:
    assert capacity.month_bounds(date(2027, 2, 14)) == (date(2027, 2, 1), date(2027, 2, 28))
    assert capacity.month_bounds(date(2027, 12, 3)) == (date(2027, 12, 1), date(2027, 12, 31))


def test_page_kpis_sum_rows() -> None:
    rows = [
        {"demand_qty": 10, "total": 100, "planning": 80, "buffer": 20, "gap": 0, "status": capacity.WITHIN},
        {"demand_qty": 150, "total": 100, "planning": 80, "buffer": 20, "gap": 50, "status": capacity.UP},
    ]
    k = capacity.page(rows)["kpis"]
    assert (k["demand"], k["total"], k["gap"], k["utilization_pct"], k["status"]) == (160, 200, 50, 80.0, capacity.UP)


# --- numbering ---


def test_plan_numbering() -> None:
    feb = date(2027, 2, 1)
    assert plans.next_number([], feb) == "PP-2027-02"
    assert plans.next_number(["PP-2027-02"], feb) == "PP-2027-02-002"
    assert plans.next_number(["PP-2027-02", "PP-2027-02-009", "PP-2027-03"], feb) == "PP-2027-02-010"


def test_wo_number_is_unique() -> None:
    taken = {"WO-270201-IMM-04-FB-ARKA"}
    assert plans.wo_number(date(2027, 2, 1), "IMM-04", "FB-ARKA", set()) == "WO-270201-IMM-04-FB-ARKA"
    assert plans.wo_number(date(2027, 2, 1), "IMM-04", "FB-ARKA", taken) == "WO-270201-IMM-04-FB-ARKA-2"


# --- state machine and wizard ---


def test_locked_statuses() -> None:
    for st in plans.LOCKED:
        with pytest.raises(FVError) as e:
            plans.assert_editable(_plan(status=st))
        assert e.value.code == "FV-PP-003"
    plans.assert_editable(_plan(status="Planning"))


def test_planned_follows_requirement_until_edited() -> None:
    ln = _line(planned=100, demand=100)
    ln["demand_qty"] = 150
    plans.follow_planned(ln, 100)
    assert ln["planned_qty"] == 150
    ln["planned_qty"] = 90
    ln["demand_qty"] = 200
    plans.follow_planned(ln, 150)
    assert ln["planned_qty"] == 90


def test_readiness_steps() -> None:
    r = plans.readiness(_plan(), [])
    assert [s["ready"] for s in r["steps"]] == [True, False, False, False, False, False]
    assert r["resume_step"] == 1
    p = _plan([_line(planned=0)], [{"order_id": "x"}])
    assert plans.readiness(p, [])["resume_step"] == 2
    p = _plan([_line()], [{"order_id": "x"}])
    r = plans.readiness(p, [])
    assert r["resume_step"] == 3
    assert r["steps"][5]["blocked_by"] == "Generate work orders first."
    w = _wo(planned_start=None, planned_end=None, machine_id=None, mold_id=None)
    r = plans.readiness(p, [w])
    assert r["resume_step"] == 4
    assert "Schedule at least one" in r["steps"][4]["blocked_by"]
    r = plans.readiness(p, [_wo(), _wo("W2", mold_id=None)])
    assert r["resume_step"] == 5
    assert r["steps"][5]["blocked_by"] == "1 work order(s) have no machine and mold yet."
    assert plans.readiness(p, [_wo()])["resume_step"] == 6


def test_step_rules() -> None:
    r = plans.readiness(_plan(step=1), [])
    for bad in (0, 7):
        with pytest.raises(FVError) as e:
            plans.assert_step(bad, r)
        assert e.value.code == "FV-PP-014"
    with pytest.raises(FVError) as e:
        plans.assert_step(3, r)
    assert e.value.code == "FV-PP-013"
    plans.assert_step(1, r)
    back = plans.readiness(_plan(step=5), [])
    plans.assert_step(4, back)


def test_derived_status() -> None:
    assert plans.derived_status(_plan(), []) == "Draft"
    assert plans.derived_status(_plan([_line()]), []) == "Planning"
    assert plans.derived_status(_plan([_line()]), [_wo(confirmed=True)]) == "Ready"
    assert plans.derived_status(_plan([_line(cap=capacity.UP)]), [_wo(confirmed=True)]) == "Planning"
    conf = _plan([_line()], status="Confirmed")
    assert plans.derived_status(conf, [_wo(status="Released")]) == "Confirmed"
    assert plans.derived_status(conf, [_wo(status="In Production"), _wo("W2", status="Released")]) == "In Execution"
    assert plans.derived_status(conf, [_wo(status="Completed")]) == "Completed"
    assert plans.derived_status(_plan([_line()], status="Cancelled"), [_wo(status="Completed")]) == "Cancelled"


# --- routing and WO generation rules ---


def test_default_routing_is_valid() -> None:
    for sku in plant.SKUS:
        assert plans.routing_problems(sku, plans.routing(sku)) == []


def test_routing_problems() -> None:
    assert plans.routing_problems("X", [])[0]["code"] == "NO_ROUTING"
    steps = [
        {"sequence": 2, "process": "Molding", "resources": ["IMM-01"]},
        {"sequence": 2, "process": "Painting", "resources": []},
        {"sequence": 5, "process": "Assembly", "resources": ["ASM-01"]},
        {"sequence": 3, "process": "Packing", "resources": ["ASM-02"], "active": False},
        {"sequence": 4, "process": "Trim", "resources": ["ASM-02"], "process_active": False},
    ]
    codes = [p["code"] for p in plans.routing_problems("X", steps)]
    assert codes.count("ROUTING_STEP_INACTIVE") == 1
    assert codes.count("PROCESS_INACTIVE") == 1
    assert codes.count("SEQUENCE_DUPLICATE") == 1
    assert codes.count("SEQUENCE_NOT_CONTINUOUS") == 3
    assert codes.count("NO_MAPPABLE_RESOURCE") == 1


def test_wo_checklist() -> None:
    assert plans.wo_checklist(_wo(), [_wo()]) == []
    bare = _wo(qty=0, machine_id=None, mold_id=None, shift=None, planned_end=None)
    assert len(plans.wo_checklist(bare, [])) == 5
    paint = _wo(process="Painting", machine_id="PNT-01", mold_id=None)
    assert plans.wo_checklist(paint, []) == []
    clash = plans.wo_checklist(_wo(), [_wo(), _wo("W2", planned_start="2027-02-01T08:00:00+07:00", planned_end="2027-02-01T10:00:00+07:00")])
    assert clash
    assert "W2" in clash[0]


def test_check_resource() -> None:
    w = _wo()
    assert plans.check_resource(w, "IMM-05", "MLD-FB-ARKA") == []
    errs = plans.check_resource(w, "IMM-01", "MLD-RB-ARKA")
    assert [e["field"] for e in errs] == ["machine_id", "mold_id"]
    assert plans.check_resource(_wo(process="Assembly"), "PNT-01", None)[0]["field"] == "machine_id"


# --- CP-SAT schedule ---


def _jobs() -> list[dict[str, Any]]:
    out = []
    for sku, qty, prio in (("FB-ARKA", 2000, 1), ("RB-ARKA", 1500, 2), ("FB-CITRA", 1000, 3)):
        pred = None
        for k, proc in enumerate(("Molding", "Painting", "Assembly"), 1):
            wid = f"{sku}-{k}"
            out.append({"wo_id": wid, "sku": sku, "process": proc, "qty": qty, "due": date(2027, 2, 6), "priority": prio, "predecessor_wo_id": pred})
            pred = wid
    return out


def test_schedule_respects_precedence_resources_and_compatibility() -> None:
    cycles = capacity.cycle_seconds(RATES, [])
    r = planning.schedule_work_orders(_jobs(), date(2027, 2, 1), cycles, HOME, time_limit_s=10)
    assert r["status"] in ("OPTIMAL", "FEASIBLE")
    assert r["solve_seconds"] < 10
    by = {a["wo_id"]: a for a in r["assignments"]}
    for j in _jobs():
        a = by[j["wo_id"]]
        assert a["planned_start"] < a["planned_end"]
        assert a["shift"] in ("A", "B", "C")
        if j["process"] == "Molding":
            assert a["machine_id"] in plant.MOLD_COMPAT[j["sku"]]
            assert a["mold_id"] == plant.MOLD_ID[j["sku"]]
        if j["predecessor_wo_id"]:
            assert a["start_minute"] >= by[j["predecessor_wo_id"]]["start_minute"] + planning.FLOW_LAG_MIN
    per: dict[str, list[tuple[int, int]]] = {}
    for a in r["assignments"]:
        per.setdefault(a["machine_id"], []).append((a["start_minute"], a["end_minute"]))
    for ivs in per.values():
        ivs.sort()
        assert all(b[0] >= a[1] for a, b in pairwise(ivs))


def test_schedule_without_compatible_machine_is_infeasible() -> None:
    r = planning.schedule_work_orders(_jobs()[:1], date(2027, 2, 1), {}, HOME)
    assert r["error"]["code"] == "FV-PP-028"

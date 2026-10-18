from datetime import date, datetime

from app import plant
from app.tools import production_plan as pp


def _plan(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "Planning",
        "wizard_step": 6,
        "lines": [],
        "demands": [],
        "work_orders": [],
        "period_start": "2027-02-01",
        "period_end": "2027-02-07",
    }
    return {**base, **over}


def test_plan_number_continues_after_the_monthly_plan() -> None:
    assert pp.next_number(date(2027, 3, 1), set()) == "PP-2027-03"
    assert pp.next_number(date(2027, 2, 9), {"PP-2027-02"}) == "PP-2027-02-002"
    assert pp.next_number(date(2027, 2, 9), {"PP-2027-02", "PP-2027-02-002"}) == "PP-2027-02-003"


def test_capacity_status_thresholds() -> None:
    assert pp._status(80, 100, 80) == "Within Plan"
    assert pp._status(81, 100, 80) == "Additional Demand"
    assert pp._status(100, 100, 80) == "Additional Demand"
    assert pp._status(101, 100, 80) == "Capacity Up Required"


def test_worst_capacity_status_of_a_plan() -> None:
    lines = [{"capacity_status": "Within Plan"}, {"capacity_status": "Capacity Up Required"}, {"capacity_status": "Additional Demand"}]
    assert pp.worst_capacity(lines) == "Capacity Up Required"
    assert pp.worst_capacity([]) is None


def test_planned_follows_demand_until_overridden() -> None:
    ln: dict[str, object] = {"planned_overridden": False, "planned_qty": 0}
    pp._set_demand(ln, 500, 100)
    assert ln["planned_qty"] == 600
    ln["planned_overridden"], ln["planned_qty"] = True, 450
    pp._set_demand(ln, 700, 100)
    assert ln["planned_qty"] == 450
    pp._set_demand(ln, 300, 0)
    assert ln["planned_qty"] == 300  # never above the demand behind it


def test_readiness_names_what_is_missing() -> None:
    r = pp.readiness(_plan())
    assert [s["reachable"] for s in r["steps"]] == [True, False, False, False, False, False]
    assert r["resume_step"] == 1
    wo = {"status": "Scheduled", "process": "MLD", "machine_id": "IMM-04", "mold_id": None}
    plan = _plan(lines=[{"planned_qty": 10, "forecast_qty": 0, "capacity_status": "Within Plan"}], demands=[{"x": 1}], work_orders=[wo])
    r = pp.readiness(plan)
    assert [s["reachable"] for s in r["steps"]] == [True, True, True, True, True, False]
    assert "1 work order(s)" in r["steps"][5]["blocked_by"]
    wo["mold_id"] = "MLD-FB-ARKA"
    assert pp.readiness(plan)["steps"][5]["reachable"]


def test_promised_minutes_fill_the_earliest_days() -> None:
    busy = pp._front_loaded({"IMM-04": 2500.0}, 3, 1188.0)
    assert busy["IMM-04"] == [1188.0, 1188.0, 124.0]


def test_work_clock_skips_sundays() -> None:
    sat_night = datetime(2027, 2, 6, 23, 0, tzinfo=plant.WIB)  # Saturday's production day ends Sunday 07:00
    t = pp._add_work(sat_night, 9 * 60)
    assert t == datetime(2027, 2, 8, 8, 0, tzinfo=plant.WIB)


def test_only_compatible_resources_are_offered() -> None:
    assert pp.resource_options({"process": "MLD", "sku": "FB-ARKA"}) == {"machines": ["IMM-04", "IMM-05"], "molds": ["MLD-FB-ARKA"]}
    assert pp.resource_options({"process": "ASM", "sku": "FB-ARKA"})["molds"] == []

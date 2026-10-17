"""Contract checks for the deterministic tools: provenance, KPI formulas, solver limits, guardrails."""

import pytest

from app import warehouse
from app.errors import FVError
from app.tools import mes, planning


def test_oee_equals_reference_query():
    r = mes.get_oee(machine_id="IMM-04")
    day = r["window"][1]
    ref = warehouse.query(
        "SELECT SUM(run_minutes) / SUM(planned_minutes) AS a, SUM(ideal_cycle_s * total_quantity) / (SUM(run_minutes) * 60) AS p, "
        "SUM(output_quantity) * 1.0 / SUM(total_quantity) AS q FROM fv.fact_production WHERE machine_id = 'IMM-04' AND production_date = :d",
        {"d": day},
    )[0]
    assert r["by_machine"]["IMM-04"]["oee_pct"] == round(100 * ref["a"] * ref["p"] * ref["q"], 1)


def test_pareto_is_sorted_and_cumulative():
    top = mes.downtime_pareto(days=30)["top"]
    assert [t["loss_idr"] for t in top] == sorted((t["loss_idr"] for t in top), reverse=True)
    assert top[-1]["cumulative_pct"] <= 100


def test_scheduler_is_feasible_fast_and_respects_molds():
    r = planning.run_scheduler(demand_change_pct={"FB-ARKA": 40})
    assert r["status"] in ("OPTIMAL", "FEASIBLE")
    assert r["solve_seconds"] < 10
    from simulator import plant as P

    for row in r["schedule"]:
        assert row["machine_id"] in P.MOLD_COMPAT[row["sku"]]


def test_maintenance_and_sunday_shifts_close_the_gap():
    res = {s["scenario"]: s for s in planning.what_if(["baseline", "maintenance_plus_sunday"], {"FB-ARKA": 40})["scenarios"]}
    assert res["maintenance_plus_sunday"]["fill_rate_pct"] > res["baseline"]["fill_rate_pct"]


def test_agents_cannot_write():
    with pytest.raises(FVError):
        warehouse.query("DELETE FROM fv.fact_production")

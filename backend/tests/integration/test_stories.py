from typing import Any

from app.tools import delivery, forecast, material, mes, planning, purchasing, quality


def within(value: float, story: dict[str, Any]) -> None:
    assert story["min"] <= value <= story["max"], f"{value} outside {story['min']}..{story['max']}"


def test_demand_forecast(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["demand_forecast"]
    r = forecast.bq_forecast(s["sku"], s["horizon_weeks"])
    within(r["change_by_final_week_pct"], s)


def test_production_planning(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["production_planning"]
    r = planning.get_capacity(demand_change_pct={"FB-ARKA": 40})
    within(r["groups"][0]["capacity_gap_pct"], s)
    assert r["bottleneck_machine"] == s["machine"]


def test_mes_breakdown_risk(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["mes"]
    r = mes.machine_health(s["machine"])
    within(r["days_to_alarm"], s)
    assert r["breakdown_risk"] == "high"
    assert r["mtbf_hours_last_2_weeks"] < r["mtbf_hours_first_6_weeks"] / 2
    others = [h for h in mes.fleet_health() if h["machine_id"] != s["machine"]]
    assert all(h["breakdown_risk"] == "low" for h in others)


def test_material_shortage(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["material_supply"]
    r = material.stock_projection(s["material"])
    within(r["days_to_stockout"], s)
    assert 0 < r["overconsumption_vs_bom_pct"] < 12


def test_purchasing_late_vendor(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["purchasing"]
    r = purchasing.vendor_performance("Resin")
    worst = r["vendors"][0]
    assert worst["vendor_name"] == s["vendor"]
    within(worst["avg_delay_days"], s)
    rec = purchasing.recommend_po("PP-EPDM-T20", need_by=material.stock_projection()["stockout_date"])
    assert rec["recommended_vendor"] != s["vendor"]


def test_quality_lot_correlation(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["quality_control"]
    r = quality.defect_correlation()
    assert r["suspect_lot"] == s["lot"]
    assert {f["defect_code"] for f in r["lot_findings"] if f["resin_lot_id"] == s["lot"]} == set(s["defects"])
    assert {f["defect_code"] for f in r["humidity_findings"]} == {s["humidity_defect"]}
    assert r["hold_candidates"]["units_in_stock"] > 0


def test_delivery_orders_at_risk(ground_truth: dict[str, Any]) -> None:
    s = ground_truth["stories"]["delivery"]
    r = delivery.orders_at_risk()
    assert sorted(o["order_id"] for o in r["orders_at_risk"]) == sorted(s["order_ids"])
    assert r["machines_involved"] == [s["machine"]]
    assert {x["sku"] for x in r["below_stock_target"]} == {"FB-ARKA"}

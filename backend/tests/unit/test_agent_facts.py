from typing import Any

from app.agents import facts


def _snap() -> dict[str, Any]:
    weeks = ["2027-02-01", "2027-02-08", "2027-02-15", "2027-02-22", "2027-03-01", "2027-03-08"]
    qty = [5200, 5500, 5800, 6000, 6300, 6480]
    lines = [
        {
            "forecast_line_id": f"FC-2027-02-001-FB-ARKA-{w}",
            "sku": "FB-ARKA",
            "customer_id": None,
            "week_start": w,
            "forecast_qty": q,
            "history_weeks_with_orders": 13,
            "history_weeks": 13,
            "insufficient_history": False,
        }
        for w, q in zip(weeks, qty, strict=True)
    ]
    lines.append(
        {
            "forecast_line_id": "FC-2027-02-001-RB-CITRA-2027-02-01",
            "sku": "RB-CITRA",
            "customer_id": None,
            "week_start": "2027-02-01",
            "forecast_qty": 40,
            "history_weeks_with_orders": 4,
            "history_weeks": 13,
            "insufficient_history": True,
        }
    )
    return {
        "forecast_id": "FC-2027-02-001",
        "status": "Generated",
        "engine": "local trend+weekday (dev)",
        "skus": ["FB-ARKA", "RB-CITRA"],
        "lines": lines,
        "history": {"FB-ARKA": [{"week_start": "2027-01-18", "qty": 4700}, {"week_start": "2027-01-25", "qty": 4800}]},
        "used_by_plans": ["PP-2027-02"],
    }


def test_trend_comes_from_the_snapshot_numbers() -> None:
    t = facts.sku_trend(_snap(), "FB-ARKA")
    assert t is not None
    assert t["change_final_week_vs_last_history_week_pct"] == 35.0
    assert t["change_first_to_final_forecast_week_pct"] == 24.6
    assert t["total_forecast_qty"] == 35280
    assert t["insufficient_history"] is False
    assert facts.sku_trend(_snap(), "FB-BROMO") is None


def test_snapshot_view_flags_insufficient_history_and_cites_the_number() -> None:
    v = facts.snapshot_view(_snap())
    assert v["forecast_id"] == "FC-2027-02-001"
    assert v["used_by_plans"] == ["PP-2027-02"]
    assert v["insufficient_history_skus"] == ["RB-CITRA"]
    assert v["skus"]["RB-CITRA"]["history_weeks_with_orders"] == 4
    assert v["skus"]["RB-CITRA"]["change_final_week_vs_last_history_week_pct"] is None
    assert "FC-2027-02-001" in v["source"]
    assert set(facts.snapshot_view(_snap(), "FB-ARKA")["skus"]) == {"FB-ARKA"}


def test_per_customer_lines_are_summed_per_week() -> None:
    s = _snap()
    s["lines"] = [{**ln, "customer_id": c, "forecast_qty": ln["forecast_qty"] // 2} for ln in s["lines"] if ln["sku"] == "FB-ARKA" for c in ("OEM-A", "OEM-B")]
    t = facts.sku_trend(s, "FB-ARKA")
    assert t is not None
    assert t["weekly_forecast"][-1]["forecast_qty"] == 6480


def test_plan_view_reports_worst_status_and_short_skus() -> None:
    plan = {
        "plan_id": "p1",
        "plan_number": "PP-2027-02",
        "status": "Draft",
        "forecast_id": "FC-2027-02-001",
        "lines": [
            {
                "sku": "FB-ARKA",
                "capacity": {"status": "Capacity Up Required", "gap": 4000, "contributions": [{"machine_id": "IMM-04"}, {"machine_id": "IMM-05"}]},
            },
            {"sku": "FB-CITRA", "capacity": {"status": "Within Plan", "gap": 0, "contributions": []}},
        ],
    }
    v = facts.plan_view(plan)
    assert v["capacity_status"] == "Capacity Up Required"
    assert v["capacity_up_required_skus"] == ["FB-ARKA"]
    assert v["lines"][0]["machines"] == ["IMM-04", "IMM-05"]
    assert facts.worst_status([]) is None

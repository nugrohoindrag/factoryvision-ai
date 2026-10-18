import pytest
from app.tools import mes


@pytest.mark.integration
def test_oee_breakdown_names_the_three_factors_worst_first() -> None:
    day = mes.get_oee()
    for machine, row in day["by_machine"].items():
        factors = [x["factor"] for x in row["losses"]]
        assert sorted(factors) == ["AVAILABILITY", "PERFORMANCE", "QUALITY"], machine
        losses = [x["loss_pct"] for x in row["losses"]]
        assert losses == sorted(losses, reverse=True), machine


@pytest.mark.integration
def test_unexplained_minutes_are_planned_minus_run_minus_downtime() -> None:
    plant = mes.get_oee(days=7)["plant_all"]
    assert plant["unexplained_minutes"] == plant["planned_minutes"] - plant["run_minutes"] - plant["downtime_minutes"]
    assert plant["unexplained_minutes"] > 0


@pytest.mark.integration
def test_pareto_categories_add_up_to_the_total() -> None:
    p = mes.downtime_pareto(days=30, top=50)
    assert abs(sum(c["hours"] for c in p["by_loss_category"]) - p["total_hours"]) < 0.5
    assert abs(sum(c["loss_idr"] for c in p["by_loss_category"]) - p["total_loss_idr"]) <= len(p["by_loss_category"])
    assert p["by_loss_category"] == sorted(p["by_loss_category"], key=lambda c: -c["loss_idr"])

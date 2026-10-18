from typing import Any

import pytest
from app.actions import IMPACT_KEYS
from app.effects import Overrides
from app.errors import FVError
from app.modules import delivery_rates
from app.tools import impact, planning
from app.tools.impact import impact_calculator, plan_total

CASES: list[tuple[str, dict[str, Any]]] = [
    ("maintenance", {"machine_id": "IMM-04"}),
    ("mold_move", {"sku": "FB-ARKA", "to_machine_id": "IMM-05"}),
    ("purchase_order", {"vendor_id": "VEN-MERAPI", "qty": 160000, "expected_date": "2027-02-06", "demand_change_pct": {"FB-ARKA": 40}}),
    ("quality_hold", {"resin_lot_id": "LOT-PPT20-0918"}),
    ("ship_date", {"order_ids": ["SO-270204-002", "SO-270205-002", "SO-270206-001"]}),
    ("extra_shift", {"machine_ids": ["IMM-04", "IMM-05"], "days": 4}),
]


@pytest.mark.parametrize(("kind", "args"), CASES)
def test_every_kind_has_four_numbers_and_assumptions(kind: str, args: dict[str, Any]) -> None:
    r = impact_calculator(kind, args)
    assert set(IMPACT_KEYS) <= set(r)
    assert all(isinstance(r[k], int) for k in IMPACT_KEYS)
    assert r["assumptions"]


def test_signs() -> None:
    assert impact_calculator("maintenance", {"machine_id": "IMM-04"})["rupiah_idr"] > 0
    assert impact_calculator("maintenance", {"machine_id": "IMM-04"})["kwh"] > 0
    assert impact_calculator("extra_shift", {"machine_ids": ["IMM-04"], "days": 4})["rupiah_idr"] < 0
    po = impact_calculator("purchase_order", {"vendor_id": "VEN-MERAPI", "qty": 160000, "expected_date": "2027-02-06"})
    assert po["breakdown"]["price_premium_idr"] < 0
    assert po["assumptions"]["stockout_days_avoided"] > 0


def test_plan_total_adds_up() -> None:
    impacts = [impact_calculator(k, a) for k, a in CASES[:2]]
    t = plan_total(impacts)
    assert t["rupiah_idr"] == impacts[0]["rupiah_idr"] + impacts[1]["rupiah_idr"]


def test_maintenance_on_a_non_imm_is_a_tool_error_not_a_crash() -> None:
    with pytest.raises(FVError) as e:
        impact_calculator("maintenance", {"machine_id": "PNT-01"})
    assert e.value.code == "FV-DATA-001"


def test_po_against_a_stockout_today_counts_the_avoided_line_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter([{"days_to_stockout": 0}, {"days_to_stockout": None}])
    monkeypatch.setattr(impact.material, "stock_projection", lambda *_a, **_k: next(calls))
    r = impact_calculator("purchase_order", {"vendor_id": "VEN-MERAPI", "qty": 160000, "expected_date": "2027-02-06"})
    assert r["assumptions"]["stockout_days_avoided"] == impact.SPOT_BUY_DAYS
    assert r["breakdown"]["avoided_line_stop_idr"] > 0


def test_mold_move_also_rerates_the_mold_swapped_back() -> None:
    back = planning.current_molds_by_machine()["IMM-05"]
    rates = delivery_rates(Overrides(mold_moves={"FB-ARKA": "IMM-05"}))
    assert {"FB-ARKA", back} <= set(rates)

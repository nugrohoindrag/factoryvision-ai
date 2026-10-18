from typing import Any

import pytest
from app.actions import IMPACT_KEYS
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

import json
import os
import uuid
from typing import Any

import httpx
import pytest

BASE = os.environ.get("FV_E2E_BASE_URL")
pytestmark = pytest.mark.skipif(not (BASE and os.environ.get("FV_E2E_LIVE") == "1"), reason="set FV_E2E_BASE_URL and FV_E2E_LIVE=1")

LiveRun = tuple[list[dict[str, Any]], list[dict[str, Any]]]


@pytest.fixture(scope="module")
def live_run() -> LiveRun:
    h = {"X-Session-Id": str(uuid.uuid4())}
    items: list[dict[str, Any]] = []
    with httpx.stream("POST", f"{BASE}/api/disruptions/disruption_response", headers=h, timeout=300) as r:
        assert r.status_code == 200, r.read()
        items.extend(json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: "))
    acts: list[dict[str, Any]] = httpx.get(f"{BASE}/api/actions", headers=h, timeout=30).json()["actions"]
    return items, acts


def _of_kind(acts: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [a for a in acts if a["kind"] == kind]


def test_run_finishes_with_one_plan(live_run: LiveRun) -> None:
    items, acts = live_run
    done = [i for i in items if i["kind"] in ("done", "error")]
    assert done
    assert done[-1]["kind"] == "done", done
    assert len({a["plan_id"] for a in acts}) == 1
    assert all(a["status"] == "pending" for a in acts)


def test_chain_covers_every_module(live_run: LiveRun) -> None:
    items, _ = live_run
    agents = {i.get("agent") for i in items if i["kind"] == "agent_start"}
    modules = {
        "demand_forecast_agent",
        "production_planning_agent",
        "material_supply_agent",
        "purchasing_agent",
        "mes_agent",
        "quality_control_agent",
        "delivery_agent",
    }
    assert modules <= agents


def test_planted_stories_become_proposals(live_run: LiveRun, ground_truth: dict[str, Any]) -> None:
    _, acts = live_run
    st = ground_truth["stories"]
    assert any(a["command"]["args"].get("machine_id") == st["mes"]["machine"] for a in _of_kind(acts, "maintenance"))
    assert any(a["command"]["args"].get("resin_lot_id") == st["quality_control"]["lot"] for a in _of_kind(acts, "quality_hold"))
    assert {a["command"]["args"]["order_id"] for a in _of_kind(acts, "ship_date")} == set(st["delivery"]["order_ids"])
    po = _of_kind(acts, "purchase_order")
    assert po
    assert all(a["command"]["args"]["vendor_id"] != "VEN-ANDALAS" for a in po)
    for a in acts:
        assert set(a["impact"]) == {"rupiah_idr", "scrap_kg", "kwh", "co2e_kg"}

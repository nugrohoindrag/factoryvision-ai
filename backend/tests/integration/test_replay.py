import uuid
from pathlib import Path
from typing import Any

import pytest
from app import plans
from app.agents import replay
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
CHAIN = {
    "demand_forecast_agent",
    "production_planning_agent",
    "material_supply_agent",
    "purchasing_agent",
    "mes_agent",
    "quality_control_agent",
    "delivery_agent",
    "orchestrator",
}


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _comparable(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: p[k] for k in ("kind", "title", "args", "impact")} for p in proposals]


def test_scripted_playbook_covers_the_chain(ground_truth: dict[str, Any]) -> None:
    rec = replay.load("disruption_response")
    assert {e["agent"] for e in rec["timeline"]} >= CHAIN
    kinds = {p["kind"] for p in rec["proposals"]}
    assert {"maintenance", "mold_move", "replan", "purchase_order", "quality_hold", "ship_date", "customer_notice"} <= kinds
    replan = next(p for p in rec["proposals"] if p["kind"] == "replan")
    assert replan["args"]["forecast_id"].startswith("FC-")
    assert replan["args"]["demand_change_pct"] == {"FB-ARKA": 40}
    assert any("capacity_delta" in e for e in replan["evidence"])
    tools = {e["tool"] for e in rec["timeline"] if e["kind"] == "tool_call"}
    assert {"get_latest_forecast_snapshot", "check_replan_trigger", "propose_replan"} <= tools
    assert replan["args"]["forecast_id"] in rec["text"]
    ship = sorted(p["args"]["order_id"] for p in rec["proposals"] if p["kind"] == "ship_date")
    assert ship == sorted(ground_truth["stories"]["delivery"]["order_ids"])
    hold = next(p for p in rec["proposals"] if p["kind"] == "quality_hold")
    assert hold["args"]["resin_lot_id"] == ground_truth["stories"]["quality_control"]["lot"]
    for p in rec["proposals"]:
        assert set(p["impact"]) == {"rupiah_idr", "scrap_kg", "kwh", "co2e_kg"}
    assert "pending your approval" in rec["text"]


def test_fresh_scripted_recording_matches_shipped_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = replay.load("disruption_response")
    if not shipped.get("scripted"):
        pytest.skip("a live recording is not reproducible by design")

    def temp_path(scenario: str) -> Path:
        return tmp_path / f"{scenario}.json"

    monkeypatch.setattr(replay, "path", temp_path)
    fresh = replay.load(replay.record("disruption_response", use_scripted=True).stem)
    assert _comparable(fresh["proposals"]) == _comparable(shipped["proposals"])


def test_play_then_approve_plan_updates_modules_and_twin() -> None:
    s = str(uuid.uuid4())
    r = client.post("/api/replay/disruption_response", headers=h(s))
    assert r.status_code == 200, r.text
    played = r.json()
    assert client.get("/api/actions", headers=h(s)).json()["pending"] == len(played["proposals"])
    before = client.get("/api/modules/delivery/summary", headers=h(s)).json()["risk"]["count"]
    client.post("/api/twin/tick", headers=h(s), json={"minutes": 30})
    d = client.post(f"/api/plans/{played['plan_id']}/approve", headers=h(s)).json()
    assert not d["failed"]
    assert len(d["decided"]) == len(played["proposals"])
    after = client.get("/api/modules/delivery/summary", headers=h(s)).json()
    assert after["risk"]["count"] < before
    assert len(after["revised_ship_dates"]) == 3
    snap = client.post("/api/twin/tick", headers=h(s), json={"minutes": 1}).json()
    m = {x["machine_id"]: x for x in snap["machines"]}
    assert m["IMM-04"]["andon"] == "blue"
    assert m["IMM-05"]["andon"] == "yellow"
    assert snap["holds"] == ["LOT-PPT20-0918"]
    assert snap["resin"]["incoming"]
    assert client.get("/api/modules/quality/summary", headers=h(s)).json()["holds"][0]["status"] == "OPEN"
    new_plans = plans.list_plans(s)
    assert len(new_plans) == 1
    assert new_plans[0]["status"] in {"Draft", "Planning"}
    assert new_plans[0]["origin"] == "agent"


def test_mode_reports_replays() -> None:
    m = client.get("/api/mode").json()
    assert "disruption_response" in m["replays"]
    assert set(m["models"]) == {"orchestrator", "module", "vision"}


def test_live_needs_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ai", "off")
    r = client.post("/api/disruptions/disruption_response", headers=h(str(uuid.uuid4())))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "FV-AI-001"

import uuid

from app.agents import replay
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)


def h(s):
    return {"X-Session-Id": s}


def test_scripted_playbook_covers_the_chain(ground_truth):
    rec = replay.load("disruption_response")
    agents = {e["agent"] for e in rec["timeline"]}
    assert {
        "demand_forecast_agent",
        "production_planning_agent",
        "material_supply_agent",
        "purchasing_agent",
        "mes_agent",
        "quality_control_agent",
        "delivery_agent",
        "orchestrator",
    } <= agents
    kinds = [p["kind"] for p in rec["proposals"]]
    assert {"maintenance", "mold_move", "purchase_order", "quality_hold", "ship_date", "customer_notice"} <= set(kinds)
    ship = sorted(p["args"]["order_id"] for p in rec["proposals"] if p["kind"] == "ship_date")
    assert ship == sorted(ground_truth["stories"]["delivery"]["order_ids"])
    hold = next(p for p in rec["proposals"] if p["kind"] == "quality_hold")
    assert hold["args"]["resin_lot_id"] == ground_truth["stories"]["quality_control"]["lot"]
    for p in rec["proposals"]:
        assert set(p["impact"]) == {"rupiah_idr", "scrap_kg", "kwh", "co2e_kg"}
    assert "pending your approval" in rec["text"]


def test_fresh_scripted_recording_matches_shipped_one(tmp_path, monkeypatch):
    shipped = replay.load("disruption_response")
    if not shipped.get("scripted"):
        return
    monkeypatch.setattr(replay, "path", lambda s: tmp_path / f"{s}.json")
    fresh = replay.load(replay.record("disruption_response", True).stem)
    strip = lambda ps: [{k: p[k] for k in ("kind", "title", "args", "impact")} for p in ps]
    assert strip(fresh["proposals"]) == strip(shipped["proposals"])


def test_play_then_approve_plan_updates_modules_and_twin():
    s = str(uuid.uuid4())
    r = client.post("/api/replay/disruption_response", headers=h(s))
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert client.get("/api/actions", headers=h(s)).json()["pending"] == len(r.json()["proposals"])
    before = client.get("/api/modules/delivery/summary", headers=h(s)).json()["risk"]["count"]
    client.post("/api/twin/tick", headers=h(s), json={"minutes": 30})
    d = client.post(f"/api/plans/{plan}/approve", headers=h(s)).json()
    assert not d["failed"] and len(d["decided"]) == len(r.json()["proposals"])
    after = client.get("/api/modules/delivery/summary", headers=h(s)).json()
    assert after["risk"]["count"] < before and len(after["revised_ship_dates"]) == 3
    snap = client.post("/api/twin/tick", headers=h(s), json={"minutes": 1}).json()
    m = {x["machine_id"]: x for x in snap["machines"]}
    assert m["IMM-04"]["andon"] == "blue" and m["IMM-05"]["andon"] == "yellow"
    assert snap["holds"] == ["LOT-PPT20-0918"] and snap["resin"]["incoming"]
    assert client.get("/api/modules/quality/summary", headers=h(s)).json()["holds"][0]["status"] == "OPEN"


def test_mode_reports_replays():
    m = client.get("/api/mode").json()
    assert "disruption_response" in m["replays"] and set(m["models"]) == {"orchestrator", "module", "vision"}


def test_live_needs_ai():
    r = client.post("/api/disruptions/disruption_response", headers=h(str(uuid.uuid4())))
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert r.json()["error"]["code"] == "FV-AI-001"

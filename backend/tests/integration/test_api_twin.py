import base64
import json
import uuid
from typing import Any

from app import actions, twin
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def m(snap: dict[str, Any], mid: str) -> dict[str, Any]:
    return next(x for x in snap["machines"] if x["machine_id"] == mid)


def test_sessions_are_isolated() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(a), json={"scenario": "disruption_response"})
    client.post("/api/demo/reset", headers=h(b), json={"scenario": "baseline"})
    sa = client.post("/api/twin/tick", headers=h(a), json={"minutes": 60}).json()
    sb = client.post("/api/twin/tick", headers=h(b), json={"minutes": 10}).json()
    assert sa["sim_minute"] == 60
    assert sb["sim_minute"] == 10
    assert m(sa, "IMM-04")["andon"] == "yellow"
    assert m(sb, "IMM-04")["andon"] == "green"


def test_tick_is_capped_and_validated() -> None:
    s = str(uuid.uuid4())
    r = client.post("/api/twin/tick", headers=h(s), json={"minutes": 500})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "FV-REQ-001"
    assert client.post("/api/twin/tick", headers=h(s), json={"minutes": 0}).json()["sim_minute"] == 0


def test_unknown_scenario() -> None:
    r = client.post("/api/demo/reset", headers=h(str(uuid.uuid4())), json={"scenario": "meteor"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FV-REQ-003"


def test_approve_reaches_twin_and_reset_clears_actions() -> None:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={"scenario": "disruption_response"})
    client.post("/api/twin/tick", headers=h(s), json={"minutes": 20})
    a = actions.propose(s, "maintenance", "Maintain IMM-04", "mes", "x", {"machine_id": "IMM-04"})
    assert client.post(f"/api/actions/{a['action_id']}/approve", headers=h(s)).json()["status"] == "approved"
    snap = client.post("/api/twin/tick", headers=h(s), json={"minutes": 1}).json()
    assert m(snap, "IMM-04")["andon"] == "blue"
    client.post("/api/demo/reset", headers=h(s), json={})
    assert client.get("/api/actions", headers=h(s)).json()["actions"] == []


def test_state_survives_instance_restart() -> None:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={"scenario": "disruption_response"})
    client.post("/api/twin/tick", headers=h(s), json={"minutes": 30})
    a = actions.propose(s, "quality_hold", "Hold", "quality", "x", {"resin_lot_id": "LOT-PPT20-0918"})
    client.post(f"/api/actions/{a['action_id']}/approve", headers=h(s))
    before = client.post("/api/twin/tick", headers=h(s), json={"minutes": 10}).json()
    twin.drop_cache()
    after = client.post("/api/twin/tick", headers=h(s), json={"minutes": 0}).json()
    for k in ("sim_minute", "wip", "warehouse", "holds", "state_hash"):
        assert before[k] == after[k], k


def test_pubsub_commands_push_envelope() -> None:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={})
    msg = {"session_id": s, "action_id": "A1", "command": {"name": "hold_lot", "args": {"resin_lot_id": "LOT-PPT20-0918"}}}
    body = {"message": {"data": base64.b64encode(json.dumps(msg).encode()).decode()}}
    r = client.post("/pubsub/commands", json=body)
    assert r.status_code == 200
    assert r.json()["result"]["units_on_hold"] > 0
    assert client.post("/pubsub/commands", json={"nope": 1}).json()["error"]["code"] == "FV-PUB-001"


def test_redelivered_command_applies_once() -> None:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={})
    msg = {"session_id": s, "action_id": "A-dup", "command": {"name": "place_po", "args": {"qty": 1000}}}
    first = twin.on_command(msg)
    assert "lot" in first
    assert twin.on_command(msg) == {"duplicate": True, "action_id": "A-dup"}
    assert len(twin.get(s).receipts) == 1

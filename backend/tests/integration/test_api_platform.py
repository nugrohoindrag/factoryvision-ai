import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, pubsub
from app.main import app
from app.modules import MODULES
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)


def _accept(_message: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


@pytest.fixture
def sid(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    s = str(uuid.uuid4())
    monkeypatch.setitem(pubsub._handlers, pubsub.AGENT_COMMANDS, _accept)
    yield s
    actions.clear(s)


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def test_session_header_required() -> None:
    r = client.get("/api/briefing")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "FV-SES-001"


def test_briefing_has_oee_losses_and_risks(sid: str, ground_truth: dict[str, Any]) -> None:
    b = client.get("/api/briefing", headers=h(sid)).json()
    assert b["oee"]["molding"]["oee_pct"] > 0
    assert len(b["top_losses"]) == 3
    assert all(x["loss_idr"] > 0 for x in b["top_losses"])
    mods = {r["module"] for r in b["risks"]}
    assert {"mes", "material", "delivery", "quality"} <= mods
    late = next(r for r in b["risks"] if r["module"] == "delivery")
    assert late["order_ids"] == ground_truth["stories"]["delivery"]["order_ids"]


@pytest.mark.parametrize("module", MODULES)
def test_every_module_summary(sid: str, module: str) -> None:
    r = client.get(f"/api/modules/{module}/summary", headers=h(sid))
    assert r.status_code == 200, r.text
    assert r.json()["module"] == module


def test_unknown_module(sid: str) -> None:
    assert client.get("/api/modules/nope/summary", headers=h(sid)).json()["error"]["code"] == "FV-REQ-002"


def test_approved_maintenance_clears_late_orders(sid: str) -> None:
    before = client.get("/api/modules/delivery/summary", headers=h(sid)).json()
    assert before["risk"]["count"] == 3
    a = actions.propose(sid, "maintenance", "Maintain IMM-04", "mes", "x", {"machine_id": "IMM-04"})
    assert client.get("/api/actions", headers=h(sid)).json()["pending"] == 1
    r = client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid))
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    after = client.get("/api/modules/delivery/summary", headers=h(sid)).json()
    assert after["risk"]["count"] < 3
    mes = client.get("/api/modules/mes/summary", headers=h(sid)).json()
    assert mes["health"]["breakdown_risk"] == "low"


def test_approved_po_moves_stockout(sid: str) -> None:
    before = client.get("/api/modules/material/summary", headers=h(sid)).json()["resin"]["days_to_stockout"]
    args = {"vendor_id": "VEN-MERAPI", "material_id": "PP-EPDM-T20", "qty": 160000, "expected_date": "2027-02-06"}
    a = actions.propose(sid, "purchase_order", "PO resin", "purchasing", "x", args)
    client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid))
    after = client.get("/api/modules/material/summary", headers=h(sid)).json()["resin"]["days_to_stockout"]
    assert after is None or after > before


def test_decided_twice_is_409(sid: str) -> None:
    a = actions.propose(sid, "maintenance", "m", "mes", "x", {"machine_id": "IMM-04"})
    client.post(f"/api/actions/{a['action_id']}/reject", headers=h(sid))
    r = client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FV-ACT-002"

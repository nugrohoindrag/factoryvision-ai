import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, pubsub
from app.documents import demo_po_facts
from app.main import app
from app.tools import forecast, orders
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


def test_order_book_counts_open_orders_by_status() -> None:
    b = orders.order_book()
    assert b["open_count"] == b["status_counts"]["Received"] + b["status_counts"]["Planned"]
    assert b["open_qty"] == sum(o["order_qty"] for o in b["open_orders"])
    assert 0 < b["on_time_delivery_pct"] <= 100
    assert {o["order_status"] for o in b["open_orders"]} <= set(orders.OPEN_STATUSES)


def test_demo_po_matches_the_schedule_on_file() -> None:
    c = orders.check_po(demo_po_facts())
    assert c["customer_id"] == "CUS-OEMA"
    assert not c["warnings"]
    assert all(line["matches_on_file"] for line in c["lines"])
    assert [(x["sku"], x["change_pct"]) for x in c["changed"]] == [("FB-ARKA", 40.0)]


def test_check_po_recomputes_change_and_flags_a_wrong_previous_qty() -> None:
    po = {
        "customer_name": "Nusantara Motor (OEM-A)",
        "lines": [{"sku": "FB-ARKA", "month": "2027-02", "qty": 20000, "previous_qty": 10000}, {"sku": "XX-1", "qty": 5}],
    }
    c = orders.check_po(po)
    assert c["lines"][0]["change_pct"] == 100.0
    assert c["lines"][0]["matches_on_file"] is False
    assert any("XX-1" in w for w in c["warnings"])


def test_forecast_vs_orders_applies_the_po_to_the_schedule_only() -> None:
    base = forecast.forecast_vs_orders("FB-ARKA")
    po = forecast.forecast_vs_orders("FB-ARKA", {"FB-ARKA": 40})
    assert [w["forecast_qty"] for w in base["weeks"]] == [w["forecast_qty"] for w in po["weeks"]]
    w1 = po["weeks"][0]
    assert w1["firm_qty"] > 0
    assert abs(w1["revised_schedule_qty"] - w1["schedule_qty"] * 1.4) <= 1
    assert po["weeks"][-1]["firm_qty"] == 0


def test_po_intake_replay_propose_approve_updates_demand(sid: str) -> None:
    r = client.post("/api/orders/po/read", data={"name": "customer-po-oem-a.pdf", "replay": "true"}, headers=h(sid))
    assert r.status_code == 200
    assert r.json()["engine"].startswith("recorded extraction")
    a = client.post("/api/orders/po/propose", json=r.json()["extracted"], headers=h(sid)).json()
    assert a["status"] == "pending"
    assert (a["kind"], a["module"]) == ("customer_po", "orders")
    assert client.get("/api/modules/orders/summary", headers=h(sid)).json()["customer_po_change_pct"] == {}
    assert client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid)).status_code == 200
    s = client.get("/api/modules/orders/summary", headers=h(sid)).json()
    assert s["customer_po_change_pct"] == {"FB-ARKA": 40.0}
    assert s["customer_pos"][0]["customer_id"] == "CUS-OEMA"
    f = client.get("/api/modules/forecast/summary", headers=h(sid)).json()
    assert f["vs_orders"]["customer_po_change_pct"] == 40.0


def test_po_without_change_is_rejected(sid: str) -> None:
    body = {"customer_name": "Garuda Auto (OEM-B)", "lines": [{"sku": "FB-BROMO", "month": "2027-02", "qty": 100, "previous_qty": 100}]}
    r = client.post("/api/orders/po/propose", json=body, headers=h(sid))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "FV-REQ-001"


def test_unknown_customer_and_sku_are_catalog_errors(sid: str) -> None:
    r = client.post("/api/orders/po/propose", json={"customer_name": "Nobody", "lines": [{"sku": "FB-ARKA", "qty": 5}]}, headers=h(sid))
    assert r.json()["error"]["code"] == "FV-DATA-001"
    assert client.get("/api/forecast/NOPE", headers=h(sid)).json()["error"]["code"] == "FV-DATA-001"


def test_forecast_detail_per_sku(sid: str) -> None:
    d = client.get("/api/forecast/RB-BROMO", headers=h(sid)).json()
    assert d["focus"]["sku"] == "RB-BROMO"
    assert len(d["vs_orders"]["weeks"]) == 6

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, pubsub
from app.documents import demo_po_facts
from app.main import app
from app.store import store
from app.tools import delivery, forecast, orders
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
    for d in store().where("order_documents", "session_id", s):
        store().delete("order_documents", d["doc_id"])


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def test_order_book_counts_open_orders_by_status() -> None:
    view = orders.order_view()
    b = orders.order_book(view)
    assert b["open_count"] == sum(b["status_counts"][s] for s in orders.OPEN_STATUSES)
    open_heads = [o for o in view["orders"] if o["status"] in orders.OPEN_STATUSES]
    assert b["open_qty"] == sum(o["ordered_qty"] for o in open_heads)
    assert 0 < b["on_time_delivery_pct"] <= 100


def test_an_order_is_a_header_with_lines_and_derived_progress() -> None:
    view = orders.order_view()
    head = next(o for o in view["orders"] if o["order_number"] == "CO-270201-OEMA")
    assert (head["order_channel"], head["po_number"], head["dock_number"]) == ("KANBAN_CARD", "KB-OEMA-270201", "D-07")
    assert [ln["line_no"] for ln in head["lines"]] == list(range(1, len(head["lines"]) + 1))
    assert {ln["sku"] for ln in head["lines"]} >= {"FB-ARKA", "RB-ARKA"}
    for ln in head["lines"]:
        assert 0 <= ln["produced_qty"] <= ln["planned_qty"] <= ln["ordered_qty"]
        assert ln["produced_qty"] == ln["from_stock_qty"]
        assert abs(ln["produced_qty"] + sum(w["qty"] for w in ln["work_orders"]) - ln["ordered_qty"]) <= 2
    assert head["status"] == "In Production"
    assert head["cancellable"] is False
    assert "already in production" in head["cancel_block_reason"]
    done = next(o for o in view["orders"] if o["status"] == "Completed")
    assert done["produced_qty"] == done["ordered_qty"]
    assert done["cancellable"] is False


def test_at_risk_orders_are_the_delivery_atp_ones() -> None:
    view = orders.order_view()
    late = {ln["order_id"] for o in view["orders"] for ln in o["lines"] if ln["at_risk"]}
    assert late == {r["order_id"] for r in delivery.orders_at_risk()["orders_at_risk"]}
    risky = [o for o in view["orders"] if o["at_risk"]]
    assert risky
    assert all(o["days_late"] > 0 and o["penalty_idr"] > 0 for o in risky)


def test_traceability_reaches_plan_and_work_orders(sid: str) -> None:
    d = client.get("/api/orders/CO-270201-OEMA", headers=h(sid)).json()
    plan = d["traceability"][0]
    assert (plan["plan_number"], plan["status"]) == ("PP-2027-02", "In Execution")
    assert any(w["status"] == "In Progress" and w["wo_id"].startswith("WO-270201-") for w in plan["work_orders"])
    old = client.get("/api/orders", params={"status": "Completed", "delivery_to": "2027-01-20"}, headers=h(sid)).json()["orders"][0]
    trace = client.get(f"/api/orders/{old['order_number']}", headers=h(sid)).json()["traceability"]
    assert trace[0]["status"] == "Completed"
    assert trace[0]["work_orders"]
    assert all(w["status"] == "Completed" for w in trace[0]["work_orders"])


def test_list_filters(sid: str) -> None:
    params = {"customer_id": "CUS-TIER1", "delivery_from": "2027-02-01", "delivery_to": "2027-02-03"}
    r = client.get("/api/orders", params=params, headers=h(sid)).json()
    assert r["orders"]
    assert all(o["customer_id"] == "CUS-TIER1" and "2027-02-01" <= o["requested_delivery_date"] <= "2027-02-03" for o in r["orders"])
    assert r["total"] > len(r["orders"])
    assert {c["value"] for c in r["channels"]} == {"PO_DOCUMENT", "KANBAN_CARD", "EMAIL", "MANUAL"}
    received = client.get("/api/orders", params={"status": "Received"}, headers=h(sid)).json()["orders"]
    assert received
    assert all(o["status"] == "Received" for o in received)


NEW = {
    "customer_id": "CUS-EXP",
    "order_channel": "PO_DOCUMENT",
    "po_number": "SET-PO-9001",
    "order_date": "2027-02-01",
    "requested_delivery_date": "2027-02-12",
    "lines": [{"sku": "FB-BROMO", "qty": 300}, {"sku": "RB-BROMO", "qty": 280, "requested_delivery_date": "2027-02-15"}],
}


def test_validate_reports_field_errors_and_warnings(sid: str) -> None:
    bad = {
        "customer_id": "",
        "order_channel": "KANBAN_CARD",
        "order_date": "2027-02-03",
        "requested_delivery_date": "2027-02-02",
        "lines": [{"sku": "FB-ARKA", "qty": 0}, {"sku": "FB-ARKA", "qty": 5}],
    }
    r = client.post("/api/orders/validate", json=bad, headers=h(sid)).json()
    assert {e["field"] for e in r["errors"]} == {"customer_id", "po_number", "requested_delivery_date", "lines.0.qty", "lines.1.sku"}
    assert r["preview"] is None
    dup = {**NEW, "po_number": "kb-oema-270203"}
    w = client.post("/api/orders/validate", json=dup, headers=h(sid)).json()
    assert w["errors"] == []
    assert any("CO-270203-OEMA" in x for x in w["warnings"])
    assert any("within a day" in x for x in w["warnings"]) is False
    assert w["preview"]["status"] in orders.OPEN_STATUSES
    soon = client.post("/api/orders/validate", json={**NEW, "requested_delivery_date": "2027-02-02"}, headers=h(sid)).json()
    assert any("within a day" in x for x in soon["warnings"])
    assert client.get("/api/actions", headers=h(sid)).json()["actions"] == []


def test_create_order_records_an_approved_action_and_feeds_delivery(sid: str) -> None:
    r = client.post("/api/orders", json=NEW, headers=h(sid))
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["created_in_session"] is True
    assert o["order_number"].startswith("CO-270212-EXP-")
    assert [ln["requested_delivery_date"] for ln in o["lines"]] == ["2027-02-12", "2027-02-15"]
    assert o["customer_pic"] == "Export desk"
    a = client.get("/api/actions", headers=h(sid)).json()["actions"]
    assert [(x["kind"], x["status"], x["origin_agent"]) for x in a] == [("customer_order", "approved", "manual_entry")]
    assert client.get("/api/orders", headers=h(sid)).json()["orders"][0]["order_number"] == o["order_number"]
    risk = client.get("/api/modules/delivery/summary", headers=h(sid)).json()
    assert risk["module"] == "delivery"
    assert any(ln["work_orders"] for ln in o["lines"])
    bad = client.post("/api/orders", json={**NEW, "lines": []}, headers=h(sid))
    assert bad.status_code == 400
    assert bad.json()["error"]["detail"]["fields"][0]["field"] == "lines"


def test_cancel_needs_a_reason_and_is_refused_once_in_production(sid: str) -> None:
    num = client.post("/api/orders", json=NEW, headers=h(sid)).json()["order"]["order_number"]
    assert client.post(f"/api/orders/{num}/cancel", json={"reason": " "}, headers=h(sid)).status_code == 400
    r = client.post(f"/api/orders/{num}/cancel", json={"reason": "Customer moved the volume to March"}, headers=h(sid))
    assert r.status_code == 200, r.text
    c = r.json()["order"]
    assert (c["status"], c["status_reason"]) == ("Cancelled", "Customer moved the volume to March")
    assert all(ln["status"] == "Cancelled" and not ln["at_risk"] for ln in c["lines"])
    assert client.post(f"/api/orders/{num}/cancel", json={"reason": "twice"}, headers=h(sid)).json()["error"]["code"] == "FV-ORD-001"
    running = client.post("/api/orders/CO-270201-OEMA/cancel", json={"reason": "test"}, headers=h(sid))
    assert running.status_code == 409
    assert "in production" in running.json()["error"]["message"]
    assert [x["kind"] for x in client.get("/api/actions", headers=h(sid)).json()["actions"]] == ["customer_order", "order_cancel"]


def test_cancelling_open_orders_frees_supply_in_delivery(sid: str) -> None:
    before = client.get("/api/modules/delivery/summary", headers=h(sid)).json()["risk"]["count"]
    assert before > 0
    later = [o for o in client.get("/api/orders", params={"delivery_from": "2027-02-02"}, headers=h(sid)).json()["orders"] if o["cancellable"]]
    for o in later:
        assert client.post(f"/api/orders/{o['order_number']}/cancel", json={"reason": "volume moved"}, headers=h(sid)).status_code == 200
    assert client.get("/api/modules/delivery/summary", headers=h(sid)).json()["risk"]["count"] < before


def test_documents_attach_list_download_and_limits(sid: str) -> None:
    num = "CO-270203-OEMA"
    r = client.post(f"/api/orders/{num}/documents", files={"file": ("kanban scan.pdf", b"%PDF-1.4 demo", "application/pdf")}, headers=h(sid))
    assert r.status_code == 200, r.text
    doc = r.json()
    d = client.get(f"/api/orders/{num}", headers=h(sid)).json()
    assert [x["file_name"] for x in d["documents"]] == ["kanban scan.pdf"]
    assert "content_b64" not in d["documents"][0]
    got = client.get(f"/api/orders/{num}/documents/{doc['doc_id']}", headers=h(sid))
    assert (got.status_code, got.content) == (200, b"%PDF-1.4 demo")
    assert client.get(f"/api/orders/{num}/documents/{doc['doc_id']}", headers=h(str(uuid.uuid4()))).status_code == 404
    exe = client.post(f"/api/orders/{num}/documents", files={"file": ("x.exe", b"MZ", "application/x-msdownload")}, headers=h(sid))
    assert exe.status_code == 400
    assert exe.json()["error"]["detail"]["fields"][0]["field"] == "file"
    assert client.post(f"/api/orders/{num}/documents", files={"file": ("big.pdf", b"0" * 800_000, "application/pdf")}, headers=h(sid)).status_code == 400
    assert client.post("/api/orders/CO-NOPE/documents", files={"file": ("a.pdf", b"1", "application/pdf")}, headers=h(sid)).status_code == 404


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

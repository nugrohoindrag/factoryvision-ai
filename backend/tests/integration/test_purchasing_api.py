import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app import actions, materials, mrp, plans, purchasing_docs, readiness, twin
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
P = "/api/purchasing"


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    yield s
    purchasing_docs.clear(s)
    mrp.clear(s)
    materials.clear(s)
    plans.clear(s)
    actions.clear(s)


def h(s: str, user: str | None = None) -> dict[str, str]:
    out = {"X-Session-Id": s}
    if user:
        out["X-User"] = user
    return out


def _ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def _err(r: Any, status: int) -> str:
    assert r.status_code == status, r.text
    code: str = r.json()["error"]["code"]
    return code


def _resin(sid: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = materials.stock(sid, "PP-EPDM-T20", "WH-RM")
    return rows[0]


def test_vendors_and_recommend(sid: str) -> None:
    vs = _ok(client.get(f"{P}/vendors", headers=h(sid)))["vendors"]
    assert {v["vendor_id"] for v in vs} == {"VEN-ANDALAS", "VEN-MERAPI", "VEN-WARNA", "VEN-KILAU", "VEN-BAJA", "VEN-RAKIT"}
    andalas = next(v for v in vs if v["vendor_id"] == "VEN-ANDALAS")
    for k in ("category", "quoted_lead_time_days", "unit_price_idr", "materials", "on_time_pct", "avg_delay_days", "effective_lead_time_days", "po_count"):
        assert k in andalas
    assert andalas["po_count"] > 0
    assert [m["material_id"] for m in andalas["materials"]] == ["PP-EPDM-T20"]
    one = _ok(client.get(f"{P}/vendors/VEN-WARNA", headers=h(sid)))
    assert "PO-2702-030" in [p["po_id"] for p in one["open_pos"]]
    assert len(one["received_pos"]) <= 10
    assert one["scorecard"]["po_count"] == one["po_count"]
    assert _err(client.get(f"{P}/vendors/VEN-NOPE", headers=h(sid)), 404) == "FV-PUR-001"
    rec = _ok(client.get(f"{P}/recommend?material_id=PP-EPDM-T20&qty=160000&need_by=2027-02-10", headers=h(sid)))
    assert rec["recommended_vendor_id"] == "VEN-MERAPI"
    for k in ("vendor_id", "vendor_name", "expected_arrival", "arrives_before_need", "unit_price_idr", "po_value_idr", "expected_delay_days", "avg_delay_days"):
        assert k in rec["options"][0]
    assert rec["premium_vs_cheapest_idr"] > 0
    paint = _ok(client.get(f"{P}/recommend?material_id=K1&qty=10", headers=h(sid)))
    assert paint["recommended_vendor_id"] == "VEN-KILAU"
    assert _err(client.get(f"{P}/recommend?material_id=K1&qty=0", headers=h(sid)), 400) == "FV-PUR-004"
    assert _err(client.get(f"{P}/recommend?material_id=NOPE&qty=1", headers=h(sid)), 404) == "FV-MAT-002"


def test_scenario_delay_moves_expected_arrival(sid: str) -> None:
    base = purchasing_docs.recommend("PP-EPDM-T20", 1000, "2027-02-10", sid=sid)
    twin.reset(sid, "vendor_late")
    late = purchasing_docs.recommend("PP-EPDM-T20", 1000, "2027-02-10", sid=sid)
    a = next(o for o in base["options"] if o["vendor_id"] == "VEN-ANDALAS")
    b = next(o for o in late["options"] if o["vendor_id"] == "VEN-ANDALAS")
    assert b["extra_delay_days"] == 3
    assert b["expected_arrival"] > a["expected_arrival"]
    assert purchasing_docs.recommend("PP-EPDM-T20", 1000, "2027-02-10") == base


def test_full_flow_over_http(sid: str) -> None:
    pr = _ok(
        client.post(
            f"{P}/requisitions",
            json={"lines": [{"material_id": "PP-EPDM-T20", "qty": 20000, "need_by": "2027-02-12"}, {"material_id": "K1", "qty": 100}], "note": "manual"},
            headers=h(sid, "ana"),
        )
    )
    assert pr["pr_id"].startswith("PR-2702-")
    assert pr["created_by"] == "ana"
    pid = pr["pr_id"]
    patched = _ok(
        client.patch(f"{P}/requisitions/{pid}", json={"lines": [{"material_id": "PP-EPDM-T20", "qty": 30000, "need_by": "2027-02-12"}]}, headers=h(sid))
    )
    assert [ln["qty"] for ln in patched["lines"]] == [30000]
    _ok(client.post(f"{P}/requisitions/{pid}/submit", headers=h(sid, "ana")))
    assert _err(client.post(f"{P}/requisitions/{pid}/reject", json={}, headers=h(sid)), 400) == "FV-PUR-011"
    assert _err(client.post(f"{P}/requisitions/{pid}/convert", headers=h(sid)), 409) == "FV-PUR-005"
    _ok(client.post(f"{P}/requisitions/{pid}/approve", headers=h(sid, "boss")))
    assert _err(client.patch(f"{P}/requisitions/{pid}", json={"note": "x"}, headers=h(sid)), 409) == "FV-PUR-005"
    conv = _ok(client.post(f"{P}/requisitions/{pid}/convert", json={"vendor_overrides": {"PP-EPDM-T20": "VEN-MERAPI"}}, headers=h(sid)))
    [po_id] = conv["po_ids"]
    got = _ok(client.get(f"{P}/requisitions/{pid}", headers=h(sid)))
    assert got["status"] == "Converted"
    assert got["po_ids"] == [po_id]
    assert [x["action"] for x in got["history"]] == ["create", "edit", "submit", "approve", "convert"]
    assert pid in [x["pr_id"] for x in _ok(client.get(f"{P}/requisitions?status=Converted", headers=h(sid)))["requisitions"]]

    before = _resin(sid)
    _ok(client.post(f"{P}/orders/{po_id}/issue", headers=h(sid)))
    issued = _resin(sid)
    assert issued["incoming_qty"] == pytest.approx(before["incoming_qty"] + 30000)
    row = next(i for i in issued["incoming"] if i["po_id"] == po_id)
    assert row["source"] == "session_po"
    po = _ok(client.get(f"{P}/orders/{po_id}", headers=h(sid)))
    assert row["expected_date"] == po["expected_date"]
    assert po["pr_id"] == pid
    assert po["origin"] == "session"
    assert po["total_value_idr"] == 30000 * 33_400

    assert (
        _err(client.post(f"{P}/orders/{po_id}/receipts", json={"lines": [{"material_id": "PP-EPDM-T20", "qty": 31501}]}, headers=h(sid)), 400) == "FV-PUR-009"
    )
    gr = _ok(client.post(f"{P}/orders/{po_id}/receipts", json={"lines": [{"material_id": "PP-EPDM-T20", "qty": 10000}]}, headers=h(sid, "wh")))
    assert gr["gr_id"].startswith("GR-2702-S-")
    assert gr["lot_id"].startswith("LOT-PPT20-S-")
    assert gr["by"] == "wh"
    assert gr["po_status"] == "Partially Received"
    after = _resin(sid)
    assert after["on_hand_qty"] == pytest.approx(before["on_hand_qty"] + 10000)
    assert after["incoming_qty"] == pytest.approx(before["incoming_qty"] + 20000)
    assert gr["stock"][0]["on_hand_qty"] == after["on_hand_qty"]
    ledger = [t for t in materials.transactions(sid, "PP-EPDM-T20") if t["source"] == "session"]
    assert [(t["type"], t["qty"]) for t in ledger] == [("Incoming", 30000.0), ("Receipt", 10000.0)]
    assert ledger[1]["ref"].startswith(gr["gr_id"])

    _ok(client.post(f"{P}/orders/{po_id}/expedite", json={"new_promised_date": "2027-02-03", "reason": "line stop"}, headers=h(sid)))
    assert (
        _err(client.post(f"{P}/orders/{po_id}/expedite", json={"new_promised_date": "2027-02-05", "reason": "line stop"}, headers=h(sid)), 400) == "FV-PUR-008"
    )
    rs = _ok(client.get(f"{P}/orders/{po_id}/receipts", headers=h(sid)))["receipts"]
    assert [r["gr_id"] for r in rs] == [gr["gr_id"]]
    assert _ok(client.get(f"{P}/receipts?po_id={po_id}", headers=h(sid)))["receipts"] == rs
    doc = client.get(f"{P}/orders/{po_id}/document", headers=h(sid))
    assert doc.status_code == 200
    assert doc.headers["content-type"].startswith("text/plain")
    assert po_id in doc.text
    closed = _ok(client.post(f"{P}/orders/{po_id}/close", headers=h(sid)))
    assert closed["status"] == "Closed"
    assert _resin(sid)["incoming_qty"] == pytest.approx(before["incoming_qty"])
    assert _err(client.post(f"{P}/orders/{po_id}/fly", headers=h(sid)), 400) == "FV-PUR-014"


def test_po_seen_by_readiness_and_mrp(sid: str) -> None:
    run = mrp.run_mrp(sid)
    net = next(r for r in run["results"] if r["material_id"] == "PP-EPDM-T20")["net_qty"]
    po = purchasing_docs.create_po(sid, vendor_id="VEN-MERAPI", lines=[{"material_id": "PP-EPDM-T20", "qty": 50000}], promised_date="2027-02-01", issue=True)
    again = mrp.run_mrp(sid)
    assert next(r for r in again["results"] if r["material_id"] == "PP-EPDM-T20")["net_qty"] == pytest.approx(net - 50000)
    check = readiness.check_plan(sid, "OEM-2027-02")
    resin = next(m for m in check["materials"] if m["material_id"] == "PP-EPDM-T20")
    assert po["po_id"] in [i["po_id"] for i in resin["incoming"]]


def test_requisition_from_mrp_and_plan(sid: str) -> None:
    run = mrp.run_mrp(sid)
    short = [r for r in run["results"] if r["net_qty"] > 0]
    pr = _ok(client.post(f"{P}/requisitions", json={"from_mrp_run": run["run_id"]}, headers=h(sid)))
    assert pr["source"] == f"mrp:{run['run_id']}"
    assert len(pr["lines"]) == len(short)
    first = next(ln for ln in pr["lines"] if ln["material_id"] == short[0]["material_id"])
    assert first["qty"] == pytest.approx(short[0]["net_qty"], abs=0.001)
    assert first["need_by"] == short[0]["required_date"]
    assert first["suggested_vendor_id"]
    again = _ok(client.post(f"{P}/requisitions", json={"from_mrp_run": run["run_id"], "material_ids": ["PP-EPDM-T20"]}, headers=h(sid)))
    assert [ln["material_id"] for ln in again["lines"]] == ["PP-EPDM-T20"]
    assert again["lines"][0]["duplicate_of"] == pr["pr_id"]
    plan = _ok(client.post(f"{P}/requisitions", json={"from_plan": "OEM-2027-02", "material_ids": ["PP-EPDM-T20", "MB-BLK"]}, headers=h(sid)))
    assert plan["source"] == "readiness:OEM-2027-02"
    assert {ln["material_id"] for ln in plan["lines"]} == {"PP-EPDM-T20", "MB-BLK"}
    assert all(ln["duplicate_of"] is None for ln in plan["lines"])
    assert _err(client.post(f"{P}/requisitions", json={"from_mrp_run": run["run_id"], "material_ids": ["NOPE"]}, headers=h(sid)), 400) == "FV-PUR-003"
    assert _err(client.post(f"{P}/requisitions", json={"from_mrp_run": "MRP-0000-00-001"}, headers=h(sid)), 404) == "FV-MRP-005"
    assert _err(client.post(f"{P}/requisitions", json={}, headers=h(sid)), 400) == "FV-PUR-003"
    assert _err(client.get(f"{P}/requisitions/PR-0000-001", headers=h(sid)), 404) == "FV-PUR-006"


def test_seed_po_receipt_lowers_its_incoming(sid: str) -> None:
    seed = _ok(client.get(f"{P}/orders/PO-2702-030", headers=h(sid)))
    assert seed["origin"] == "seed"
    assert seed["status"] == "Issued"
    inc0 = {i["po_id"]: i["qty"] for i in materials.incoming(sid, "B-C07")}
    oh0 = materials.stock(sid, "B-C07")[0]["on_hand_qty"]
    gr = _ok(client.post(f"{P}/orders/PO-2702-030/receipts", json={"lines": [{"material_id": "B-C07", "qty": 70}]}, headers=h(sid)))
    assert gr["po_status"] == "Partially Received"
    assert {i["po_id"]: i["qty"] for i in materials.incoming(sid, "B-C07")}["PO-2702-030"] == pytest.approx(inc0["PO-2702-030"] - 70)
    assert materials.stock(sid, "B-C07")[0]["on_hand_qty"] == pytest.approx(oh0 + 70)
    _ok(client.post(f"{P}/orders/PO-2702-030/receipts", json={"lines": [{"material_id": "B-C07", "qty": 50}]}, headers=h(sid)))
    assert "PO-2702-030" not in [i["po_id"] for i in materials.incoming(sid, "B-C07")]
    assert _ok(client.get(f"{P}/orders/PO-2702-030", headers=h(sid)))["status"] == "Received"
    assert _err(client.post(f"{P}/orders/PO-2702-030/close", headers=h(sid)), 409) == "FV-PUR-013"
    other = str(uuid.uuid4())
    assert _ok(client.get(f"{P}/orders/PO-2702-030", headers=h(other)))["status"] == "Issued"
    assert {i["po_id"]: i["qty"] for i in materials.incoming(other, "B-C07")} == inc0


def test_orders_list_filters_and_summary(sid: str) -> None:
    seed_only = _ok(client.get(f"{P}/orders", headers=h(sid)))["orders"]
    assert {p["origin"] for p in seed_only} == {"seed"}
    assert all(p["status"] == "Issued" for p in seed_only)
    late = _ok(
        client.post(
            f"{P}/orders",
            json={"vendor_id": "VEN-KILAU", "lines": [{"material_id": "K1", "qty": 10}], "promised_date": "2027-01-20", "issue": True},
            headers=h(sid),
        )
    )
    assert late["status"] == "Issued"
    assert late["overdue"] is True
    assert late["days_late"] > 0
    assert _err(client.post(f"{P}/orders", json={"vendor_id": "VEN-KILAU", "lines": [{"material_id": "P1", "qty": 1}]}, headers=h(sid)), 400) == "FV-PUR-007"
    assert _err(client.post(f"{P}/orders", json={"vendor_id": "VEN-X", "lines": [{"material_id": "K1", "qty": 1}]}, headers=h(sid)), 404) == "FV-PUR-001"
    overdue = _ok(client.get(f"{P}/orders?overdue=true", headers=h(sid)))["orders"]
    assert late["po_id"] in [p["po_id"] for p in overdue]
    assert all(p["overdue"] for p in overdue)
    assert next(p["po_id"] for p in _ok(client.get(f"{P}/orders?vendor_id=VEN-KILAU&material_id=K1", headers=h(sid)))["orders"]) == late["po_id"]
    assert _err(client.get(f"{P}/orders?status=Lost", headers=h(sid)), 400) == "FV-PUR-015"
    pr = purchasing_docs.create_requisition(sid, lines=[{"material_id": "K2", "qty": 3}])
    purchasing_docs.transition_requisition(sid, pr["pr_id"], "submit", user="u")
    s = _ok(client.get("/api/modules/purchasing/summary", headers=h(sid)))
    for k in ("scorecard", "resin_po", "placed"):
        assert k in s
    assert {"open_pos", "overdue_pos", "open_value_idr", "pending_requisitions"} <= set(s["kpis"])
    assert s["kpis"]["pending_requisitions"] == 1
    assert late["po_id"] in [p["po_id"] for p in s["overdue"]]
    assert [p["pr_id"] for p in s["pending_requisitions"]] == [pr["pr_id"]]
    assert _ok(client.get(f"{P}/summary", headers=h(sid)))["kpis"] == s["kpis"]
    other = _ok(client.get("/api/modules/purchasing/summary", headers=h(str(uuid.uuid4()))))
    assert other["kpis"]["pending_requisitions"] == 0
    assert late["po_id"] not in [p["po_id"] for p in other["overdue"]]


def test_reset_clears_purchasing(sid: str) -> None:
    po = purchasing_docs.create_po(sid, vendor_id="VEN-KILAU", lines=[{"material_id": "K1", "qty": 10}], issue=True)
    purchasing_docs.receive(sid, po["po_id"], [{"material_id": "K1", "qty": 4}])
    purchasing_docs.create_requisition(sid, lines=[{"material_id": "K2", "qty": 3}])
    assert _ok(client.post("/api/demo/reset", json={"scenario": "baseline"}, headers=h(sid)))
    assert purchasing_docs.list_requisitions(sid) == []
    assert purchasing_docs.list_receipts(sid) == []
    assert all(p["origin"] == "seed" for p in purchasing_docs.list_pos(sid))
    assert "session_po" not in {i["source"] for i in materials.incoming(sid)}


def test_session_po_replaces_approved_po_incoming(sid: str) -> None:
    args = {"vendor_id": "VEN-MERAPI", "material_id": "PP-EPDM-T20", "qty": 1000, "expected_date": "2027-02-06"}
    a = actions.propose(sid, "purchase_order", "PO resin", "purchasing", "x", args)
    actions.decide(sid, a["action_id"], approve=True, user="boss")
    out = actions.get(sid, a["action_id"])["execution"]["result"]
    srcs = [(i["source"], i["po_id"]) for i in materials.incoming(sid, "PP-EPDM-T20")]
    assert ("approved_po", a["action_id"]) not in srcs
    assert [p for s_, p in srcs if s_ == "session_po"] == [out["session_po_id"]]
    srcs = [i["source"] for i in materials.incoming(sid, "PP-EPDM-T20")]
    assert srcs.count("session_po") == 1
